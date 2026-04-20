"""Inbound provider auth/sync endpoints for Gmail and Outlook connections."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.state import engine
from app.utils import is_enabled_flag
from app.services.outlook_inbound_service import (
    clear_auth_status,
    get_auth_status,
    get_outlook_token_status,
    reset_outlook_auth,
    start_outlook_auth,
    sync_outlook_connection,
)
from app.services.outlook_webhook_service import ensure_outlook_subscription

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inbound-auth", tags=["Inbound Auth"])


class StartInboundAuthRequest(BaseModel):
    client_id: str = ""


def _ensure_inbound_enabled(conn: dict) -> None:
    if not is_enabled_flag(conn.get("enabled", True)):
        raise HTTPException(status_code=409, detail=f"Inbound connection '{conn.get('id')}' is disabled.")


def _get_conn(conn_id: str) -> dict:
    try:
        conn = engine.registry.get(conn_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Connection '{conn_id}' not found.") from exc
    if conn.get("direction") != "inbound":
        raise HTTPException(status_code=400, detail=f"Connection '{conn_id}' is not inbound.")
    return conn


def _notif_receiver_candidates(path: str) -> list[str]:
    """Return candidate notif_receiver URLs, including Docker-safe fallbacks."""
    primary_base = get_settings().notif_receiver_url.rstrip("/")
    candidates = [f"{primary_base}{path}"]

    parsed = urlparse(primary_base)
    host = (parsed.hostname or "").lower()
    # Inside containers, localhost points to the current container.
    if host in {"localhost", "127.0.0.1"}:
        scheme = parsed.scheme or "http"
        port = parsed.port or 8000
        candidates.append(f"{scheme}://gmail-notif-receiver:{port}{path}")
        candidates.append(f"{scheme}://host.docker.internal:{port}{path}")

    # de-duplicate while preserving order
    unique: list[str] = []
    for url in candidates:
        if url not in unique:
            unique.append(url)
    return unique


def _gmail_proxy(method: str, path: str, payload: dict | None = None) -> dict:
    last_error: Exception | None = None
    resp = None
    for url in _notif_receiver_candidates(path):
        try:
            resp = requests.request(method=method, url=url, json=payload, timeout=20)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("notif_receiver probe failed for %s: %s", url, exc)

    if resp is None:
        detail = f"notif_receiver is unreachable: {last_error}" if last_error else "notif_receiver is unreachable"
        raise HTTPException(status_code=502, detail=detail)

    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:600])

    if resp.status_code == 204:
        return {"ok": True}
    try:
        return resp.json()
    except Exception:
        return {"ok": True}


@router.post("/{conn_id}/start")
def start_inbound_auth(conn_id: str, body: StartInboundAuthRequest = StartInboundAuthRequest()):
    conn = _get_conn(conn_id)
    _ensure_inbound_enabled(conn)
    ctype = conn.get("type")

    if ctype == "gmail":
        return _gmail_proxy("POST", "/gmail/auth/start")

    if ctype in {"outlook", "outlook365"}:
        try:
            return start_outlook_auth(conn_id, body.client_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    raise HTTPException(status_code=400, detail=f"Unsupported inbound connection type '{ctype}'.")


@router.get("/{conn_id}/status")
def get_inbound_auth_status(conn_id: str):
    conn = _get_conn(conn_id)
    ctype = conn.get("type")

    if ctype == "gmail":
        status = _gmail_proxy("GET", "/gmail/auth/status")
        flow = status.get("flow_status", "idle")
        return {
            "status": flow,
            "message": status.get("flow_message"),
            "token_status": status.get("token_status", "missing"),
            "token_expiry": status.get("token_expiry"),
            "scopes": status.get("scopes", []),
            "provider": "gmail",
        }

    if ctype in {"outlook", "outlook365"}:
        s = get_auth_status(conn_id)
        s["provider"] = ctype
        # Enrich with the true token state from the persisted MSAL cache whenever
        # there is no active auth flow in progress.
        if s.get("status") not in {"pending"}:
            token_info = get_outlook_token_status(conn_id)
            s.update(token_info)
            # Upgrade "idle" → "success" when the token is actually valid.
            if token_info.get("token_status") == "valid" and s.get("status") == "idle":
                s["status"] = "success"
        return s

    raise HTTPException(status_code=400, detail=f"Unsupported inbound connection type '{ctype}'.")


@router.delete("/{conn_id}/status")
def clear_inbound_auth_status(conn_id: str):
    conn = _get_conn(conn_id)
    ctype = conn.get("type")

    if ctype == "gmail":
        return _gmail_proxy("DELETE", "/gmail/auth/status")

    if ctype in {"outlook", "outlook365"}:
        return clear_auth_status(conn_id)

    raise HTTPException(status_code=400, detail=f"Unsupported inbound connection type '{ctype}'.")


@router.post("/{conn_id}/reset-auth")
def reset_inbound_auth(conn_id: str):
    """Wipe the stored OAuth token for an Outlook connection, forcing re-authentication."""
    conn = _get_conn(conn_id)
    ctype = conn.get("type")

    if ctype in {"outlook", "outlook365"}:
        try:
            return reset_outlook_auth(conn_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    raise HTTPException(status_code=400, detail=f"Reset auth is not supported for connection type '{ctype}'.")


@router.post("/{conn_id}/sync")
def sync_inbound_connection(conn_id: str):
    """
    Trigger immediate sync for a specific inbound connection.

    - Gmail: renew/start Gmail watch in notif_receiver.
    - Outlook: pull new inbox messages via Microsoft Graph and process rules.
    """
    conn = _get_conn(conn_id)
    _ensure_inbound_enabled(conn)
    ctype = conn.get("type")

    if ctype == "gmail":
        watch = _gmail_proxy("POST", "/gmail/watch")
        return {"provider": "gmail", "status": "ok", "watch": watch}

    if ctype in {"outlook", "outlook365"}:
        try:
            subscription = ensure_outlook_subscription(conn_id)
            result = sync_outlook_connection(conn_id)
            return {"provider": ctype, "status": "ok", "subscription": subscription, **result}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    raise HTTPException(status_code=400, detail=f"Unsupported inbound connection type '{ctype}'.")
