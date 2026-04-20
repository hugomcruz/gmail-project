"""
OneDrive OAuth device code flow — API endpoints.

Flow:
  1. POST /api/onedrive-auth/{conn_id}/start
       → initiates device code flow, returns {user_code, verification_url, expires_at}
       → spawns a background thread that blocks on acquire_token_by_device_flow()
          and saves the token cache to disk on success.

  2. GET /api/onedrive-auth/{conn_id}/status
       → returns {status: "pending"|"success"|"error", message?}
       → UI polls this every few seconds until status != "pending"
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import msal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onedrive-auth", tags=["OneDrive Auth"])

AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["Files.ReadWrite"]

AUTHORITY_365 = "https://login.microsoftonline.com/organizations"
# Files.ReadWrite.All covers both OneDrive for Business and SharePoint file
# access as a delegated permission, without requiring admin consent.
SCOPES_365 = ["Files.ReadWrite.All"]

ONEDRIVE_TYPES = {"onedrive", "onedrive365"}

# In-memory state keyed by connection ID
# { conn_id: {"status": "pending"|"success"|"error", "message": str} }
_auth_state: dict[str, dict[str, Any]] = {}
_state_lock = threading.Lock()

# Holds the active MSAL app + cache for in-progress device-code flows.
# Using the SAME app instance for both initiate_device_flow and
# acquire_token_by_device_flow avoids issues with multi-instance MSAL state.
_auth_sessions: dict[str, dict[str, Any]] = {}


class StartRequest(BaseModel):
    """Optional body — supply client_id directly for unsaved connections."""
    client_id: str = ""
    token_cache: str = ""  # kept for UI compatibility, no longer used as a file path


def _get_onedrive_conn(conn_id: str) -> dict[str, Any]:
    from app.state import engine
    try:
        conn = engine.registry.get(conn_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Connection '{conn_id}' not found.")
    if conn.get("type") not in ONEDRIVE_TYPES:
        raise HTTPException(status_code=400, detail=f"Connection '{conn_id}' is not an OneDrive type.")
    return conn


def _authority_and_scopes(conn_type: str, tenant_id: str = "") -> tuple[str, list[str]]:
    """Return the correct MSAL authority and scopes for the connection type."""
    if conn_type == "onedrive365":
        authority = f"https://login.microsoftonline.com/{tenant_id}" if tenant_id else AUTHORITY_365
        return authority, SCOPES_365
    return AUTHORITY, SCOPES


def _load_cache_from_db(conn_id: str) -> str | None:
    """Return the serialised MSAL token cache stored in the connection's DB record."""
    try:
        from app.db.database import get_session_factory
        from app.db import crud
        SessionLocal = get_session_factory()
        with SessionLocal() as db:
            conn = crud.get_connection(db, conn_id)
            if conn:
                return (conn.fields or {}).get("_msal_cache") or None
    except Exception as exc:
        logger.warning("Could not load OneDrive token cache for '%s': %s", conn_id, exc)
    return None


def _save_cache_to_db(conn_id: str, cache_data: str) -> None:
    """Persist the serialised MSAL token cache into the connection's DB record."""
    logger.info("_save_cache_to_db called for connection '%s' (cache length=%d)", conn_id, len(cache_data))
    from app.db.database import get_session_factory
    from app.db import crud
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        conn = crud.get_connection(db, conn_id)
        if conn:
            fields = dict(conn.fields or {})
            fields["_msal_cache"] = cache_data
            crud.update_connection(db, conn_id, conn.direction, conn.type, fields)
            logger.info("OneDrive token cache persisted to DB for connection '%s'.", conn_id)
        else:
            logger.warning(
                "Connection '%s' not found in DB during cache save — creating a new record.", conn_id
            )
            crud.create_connection(db, conn_id, "outbound", "onedrive365", {"_msal_cache": cache_data})
    # Reload registry so the refreshed cache is immediately available
    from app.state import engine
    engine.reload_connections()


def _auth_thread(conn_id: str, flow: dict) -> None:
    """Background thread: wait for the user to complete device code auth.

    Uses the SAME msal.PublicClientApplication that initiated the flow
    (stored in _auth_sessions) to avoid cross-instance state issues.
    """
    logger.info("_auth_thread started for connection '%s' — waiting for user to complete sign-in", conn_id)
    try:
        with _state_lock:
            session = _auth_sessions.get(conn_id, {})

        app: msal.PublicClientApplication | None = session.get("app")
        cache: msal.SerializableTokenCache | None = session.get("cache")

        if app is None or cache is None:
            raise RuntimeError("No auth session found — the flow may have already expired.")

        result = app.acquire_token_by_device_flow(flow)
        logger.info(
            "_auth_thread '%s': acquire_token_by_device_flow returned keys=%s error=%s",
            conn_id,
            list(result.keys()),
            result.get("error") or result.get("error_description") or "none",
        )

        if "access_token" in result:
            try:
                _save_cache_to_db(conn_id, cache.serialize())
                logger.info("OneDrive auth successful for connection '%s', token saved to DB", conn_id)
                with _state_lock:
                    _auth_state[conn_id] = {
                        "status": "success",
                        "message": "Authentication successful. Token saved.",
                    }
            except Exception as save_exc:
                logger.error("OneDrive auth succeeded but token save FAILED for '%s': %s", conn_id, save_exc)
                with _state_lock:
                    _auth_state[conn_id] = {
                        "status": "error",
                        "message": f"Authenticated but failed to save token: {save_exc}",
                    }
        else:
            error = result.get("error_description") or result.get("error") or "Unknown error"
            logger.error("OneDrive auth failed for '%s': %s", conn_id, error)
            with _state_lock:
                _auth_state[conn_id] = {"status": "error", "message": error}

    except Exception as exc:
        logger.exception("OneDrive auth thread error for '%s': %s", conn_id, exc)
        with _state_lock:
            _auth_state[conn_id] = {"status": "error", "message": str(exc)}
    finally:
        # Always clean up the in-memory session
        with _state_lock:
            _auth_sessions.pop(conn_id, None)


@router.post("/{conn_id}/start")
def start_auth(conn_id: str, body: StartRequest = StartRequest()):
    """
    Initiate OneDrive device code flow.
    client_id is resolved in priority order:
      1. body.client_id (UI override)
      2. connection record's client_id field
      3. ONEDRIVE_CLIENT_ID / AZURE_CLIENT_ID env var / settings
    Returns {user_code, verification_url, message, expires_at}.
    """
    logger.info("start_auth called for connection '%s'", conn_id)

    # Determine connection type (personal vs work/365)
    conn_type = "onedrive"
    tenant_id = ""
    try:
        conn = _get_onedrive_conn(conn_id)
        conn_type = conn.get("type", "onedrive")
        tenant_id = str(conn.get("tenant_id") or "")
    except HTTPException:
        pass

    authority, scopes = _authority_and_scopes(conn_type, tenant_id)
    logger.info("start_auth '%s': type=%s authority=%s scopes=%s", conn_id, conn_type, authority, scopes)

    # Resolve client_id
    if body.client_id:
        client_id = body.client_id
    else:
        try:
            conn = _get_onedrive_conn(conn_id)
            client_id = conn.get("client_id", "")
        except HTTPException:
            client_id = ""

    if not client_id:
        s = get_settings()
        client_id = (s.onedrive_client_id or s.outlook_client_id or "")

    logger.info("start_auth '%s': resolved client_id=%s", conn_id, client_id[:8] + "..." if client_id else "(empty)")

    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="No client_id configured. Set AZURE_CLIENT_ID in the server environment."
        )

    with _state_lock:
        existing = _auth_state.get(conn_id, {})
        if existing.get("status") == "pending":
            return existing

    try:
        # Load existing token cache and build a single MSAL app instance.
        # The SAME instance is reused for both initiate_device_flow and
        # acquire_token_by_device_flow to avoid cross-instance state issues.
        cache = msal.SerializableTokenCache()
        cache_data = _load_cache_from_db(conn_id)
        if cache_data:
            cache.deserialize(cache_data)

        app = msal.PublicClientApplication(
            client_id=client_id,
            authority=authority,
            token_cache=cache,
        )

        # If a valid token already exists in the cache, report success immediately
        accs = app.get_accounts()
        if accs:
            silent = app.acquire_token_silent(scopes, account=accs[0])
            if silent and "access_token" in silent:
                if cache.has_state_changed:
                    _save_cache_to_db(conn_id, cache.serialize())
                with _state_lock:
                    _auth_state[conn_id] = {
                        "status": "success",
                        "message": "Already authenticated (token is valid).",
                    }
                return _auth_state[conn_id]

        flow = app.initiate_device_flow(scopes=scopes)

        if "error" in flow:
            raise HTTPException(status_code=500,
                                detail=f"Failed to initiate device flow: {flow.get('error_description', flow['error'])}")

        expires_at = datetime.fromtimestamp(
            time.time() + flow.get("expires_in", 900), tz=timezone.utc
        ).isoformat()

        state = {
            "status": "pending",
            "user_code": flow["user_code"],
            "verification_url": flow["verification_uri"],
            "message": flow.get("message", f"Go to {flow['verification_uri']} and enter code {flow['user_code']}"),
            "expires_at": expires_at,
        }
        with _state_lock:
            _auth_state[conn_id] = state
            # Store the app + cache so the background thread can use the same instance
            _auth_sessions[conn_id] = {"app": app, "cache": cache}

        # Spawn background thread to wait for user and save token
        t = threading.Thread(
            target=_auth_thread,
            args=(conn_id, flow),
            daemon=True,
        )
        t.start()

        return state

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{conn_id}/status")
def get_status(conn_id: str):
    """Poll for the current auth status of a connection."""
    with _state_lock:
        state = _auth_state.get(conn_id)
    if not state:
        # No in-progress flow — check DB for a stored token to show correct status
        cache_data = _load_cache_from_db(conn_id)
        if cache_data:
            try:
                conn_type = "onedrive"
                tenant_id = ""
                try:
                    conn = _get_onedrive_conn(conn_id)
                    conn_type = conn.get("type", "onedrive")
                    tenant_id = str(conn.get("tenant_id") or "")
                except HTTPException:
                    pass
                authority, scopes = _authority_and_scopes(conn_type, tenant_id)
                tmp_cache = msal.SerializableTokenCache()
                tmp_cache.deserialize(cache_data)
                s = get_settings()
                client_id = s.onedrive_client_id or s.outlook_client_id or ""
                if client_id:
                    chk_app = msal.PublicClientApplication(
                        client_id=client_id, authority=authority, token_cache=tmp_cache
                    )
                    accs = chk_app.get_accounts()
                    if accs:
                        silent = chk_app.acquire_token_silent(scopes, account=accs[0])
                        if silent and "access_token" in silent:
                            return {"status": "success", "message": "Token is valid."}
            except Exception:
                pass
        return {"status": "idle"}
    return state


@router.delete("/{conn_id}/status")
def clear_status(conn_id: str):
    """Clear auth state (so a new flow can be started)."""
    with _state_lock:
        _auth_state.pop(conn_id, None)
    return {"cleared": True}
