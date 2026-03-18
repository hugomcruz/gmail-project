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

# In-memory state keyed by connection ID
# { conn_id: {"status": "pending"|"success"|"error", "message": str} }
_auth_state: dict[str, dict[str, Any]] = {}
_state_lock = threading.Lock()


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
    if conn.get("type") != "onedrive":
        raise HTTPException(status_code=400, detail=f"Connection '{conn_id}' is not type 'onedrive'.")
    return conn


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
    from app.db.database import get_session_factory
    from app.db import crud
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        conn = crud.get_connection(db, conn_id)
        if conn:
            fields = dict(conn.fields or {})
            fields["_msal_cache"] = cache_data
            crud.update_connection(db, conn_id, conn.type, fields)
            logger.info("OneDrive token cache persisted to DB for connection '%s'.", conn_id)
        else:
            # Connection not yet in DB (e.g. loaded only from YAML) — create it now.
            logger.warning(
                "Connection '%s' not found in DB during cache save — creating a new record.", conn_id
            )
            crud.create_connection(db, conn_id, "onedrive", {"_msal_cache": cache_data})
    # Reload registry so the refreshed cache is immediately available
    from app.state import engine
    engine.reload_connections()


def _auth_thread(conn_id: str, client_id: str, flow: dict) -> None:
    """Background thread: wait for the user to complete device code auth."""
    try:
        cache = msal.SerializableTokenCache()
        existing = _load_cache_from_db(conn_id)
        if existing:
            cache.deserialize(existing)

        app = msal.PublicClientApplication(
            client_id=client_id,
            authority=AUTHORITY,
            token_cache=cache,
        )

        result = app.acquire_token_by_device_flow(flow)

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


@router.post("/{conn_id}/start")
def start_auth(conn_id: str, body: StartRequest = StartRequest()):
    """
    Initiate OneDrive device code flow.
    client_id is resolved in priority order:
      1. body.client_id (UI override)
      2. connection record's client_id field
      3. ONEDRIVE_CLIENT_ID env var / settings
    Returns {user_code, verification_url, message, expires_at}.
    """
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
        client_id = get_settings().onedrive_client_id

    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="No OneDrive client_id configured. Set ONEDRIVE_CLIENT_ID in the server environment."
        )

    with _state_lock:
        existing = _auth_state.get(conn_id, {})
        if existing.get("status") == "pending":
            # Return the existing flow info so the UI can re-render the code
            return existing

    try:
        app = msal.PublicClientApplication(client_id=client_id, authority=AUTHORITY)

        # If a valid token already exists in the DB, report success immediately
        cache_data = _load_cache_from_db(conn_id)
        if cache_data:
            tmp_cache = msal.SerializableTokenCache()
            tmp_cache.deserialize(cache_data)
            app2 = msal.PublicClientApplication(
                client_id=client_id, authority=AUTHORITY, token_cache=tmp_cache
            )
            accs = app2.get_accounts()
            if accs:
                silent = app2.acquire_token_silent(SCOPES, account=accs[0])
                if silent and "access_token" in silent:
                    with _state_lock:
                        _auth_state[conn_id] = {
                            "status": "success",
                            "message": "Already authenticated (token is valid).",
                        }
                    return _auth_state[conn_id]

        flow = app.initiate_device_flow(scopes=SCOPES)

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

        # Spawn background thread to wait for user and save token
        t = threading.Thread(
            target=_auth_thread,
            args=(conn_id, client_id, flow),
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
        return {"status": "idle"}
    return state


@router.delete("/{conn_id}/status")
def clear_status(conn_id: str):
    """Clear auth state (so a new flow can be started)."""
    with _state_lock:
        _auth_state.pop(conn_id, None)
    return {"cleared": True}
