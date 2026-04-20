"""Outlook inbound auth + message sync for inbound connections."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import msal
import requests

from app.config import get_settings
from app.db import crud
from app.db.database import get_session_factory
from app.services.email_processing import process_inbound_email
from app.state import engine
from app.utils import is_enabled_flag

logger = logging.getLogger(__name__)

_AUTHORITY_BASE = "https://login.microsoftonline.com"
# Device-code flow should request delegated Graph scopes directly.
# Do not include reserved scopes like offline_access/openid/profile explicitly.
SCOPES = ["Mail.Read"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Accepted inbound connection types served by this module.
_OUTLOOK_TYPES = {"outlook", "outlook365"}

# In-memory auth flow state keyed by connection ID
_auth_state: dict[str, dict[str, Any]] = {}
_state_lock = threading.Lock()


def _get_authority(fields: dict[str, Any], conn_type: str = "outlook") -> str:
    """Return the MSAL authority URL for this connection.

    Resolution order:
    - Explicit ``tenant_id`` → use that segment directly (UUID, domain,
      ``organizations``, ``common``, or ``consumers``).
    - ``outlook365`` with no tenant_id → ``/organizations`` (work/school).
    - ``outlook`` with no tenant_id → ``/consumers`` (personal accounts).
    """
    tenant = str(fields.get("tenant_id") or "").strip()
    if tenant:
        return f"{_AUTHORITY_BASE}/{tenant}"
    # 'consumers' for personal-only (outlook), 'common' for any Microsoft account
    # (outlook365 without a specific tenant accepts both org and personal accounts).
    default_segment = "consumers" if conn_type == "outlook" else "common"
    return f"{_AUTHORITY_BASE}/{default_segment}"


def _get_outlook_connection(conn_id: str) -> dict[str, Any]:
    try:
        conn = engine.registry.get(conn_id)
    except KeyError as exc:
        raise ValueError(f"Connection '{conn_id}' not found.") from exc

    if conn.get("direction") != "inbound" or conn.get("type") not in _OUTLOOK_TYPES:
        raise ValueError(f"Connection '{conn_id}' is not an inbound outlook connection.")
    if not is_enabled_flag(conn.get("enabled", True)):
        raise ValueError(f"Connection '{conn_id}' is disabled.")
    return conn


def _load_connection_fields(conn_id: str) -> tuple[str, dict[str, Any]]:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        conn = crud.get_connection(db, conn_id)
        if not conn:
            raise ValueError(f"Connection '{conn_id}' not found in database.")
        return conn.type, dict(conn.fields or {})


def _save_connection_fields(conn_id: str, fields: dict[str, Any]) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        conn = crud.get_connection(db, conn_id)
        if not conn:
            raise ValueError(f"Connection '{conn_id}' not found in database.")
        crud.update_connection(db, conn_id, conn.direction, conn.type, fields)
    engine.reload_connections()


def _to_graph_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _build_msal_app(client_id: str, cache_data: str | None = None, authority: str = "") -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    cache = msal.SerializableTokenCache()
    if cache_data:
        cache.deserialize(cache_data)
    auth = authority or f"{_AUTHORITY_BASE}/consumers"
    app = msal.PublicClientApplication(client_id=client_id, authority=auth, token_cache=cache)
    return app, cache


def _get_access_token(conn_id: str, fields: dict[str, Any], conn_type: str = "outlook") -> tuple[str, dict[str, Any]]:
    client_id = (fields.get("client_id") or get_settings().outlook_client_id or "").strip()
    if not client_id:
        raise RuntimeError("No Outlook client_id configured. Set OUTLOOK_CLIENT_ID or connection field client_id.")

    app, cache = _build_msal_app(client_id, fields.get("_msal_cache"), _get_authority(fields, conn_type))
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError("Outlook connection is not authenticated yet.")

    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError("Outlook access token missing or expired. Re-authenticate connection.")

    if cache.has_state_changed:
        fields["_msal_cache"] = cache.serialize()

    return result["access_token"], fields


def _extract_email_payload(message: dict[str, Any], conn_id: str) -> dict[str, Any]:
    from_addr = ((message.get("from") or {}).get("emailAddress") or {}).get("address", "")
    to_recipients = message.get("toRecipients") or []
    to_values = [((r.get("emailAddress") or {}).get("address") or "") for r in to_recipients]
    body = message.get("body") or {}
    body_content = body.get("content") or ""
    body_type = (body.get("contentType") or "").lower()

    payload: dict[str, Any] = {
        "id": message.get("internetMessageId") or message.get("id") or "",
        "provider": "outlook",
        "connection_id": conn_id,
        "subject": message.get("subject") or "(no subject)",
        "from": from_addr,
        "to": ", ".join([x for x in to_values if x]),
        "date": message.get("receivedDateTime"),
        "headers": {},
        "attachments": [],
    }

    if body_type == "html":
        payload["body_html"] = body_content
        payload["body_text"] = ""
    else:
        payload["body_text"] = body_content
        payload["body_html"] = ""

    return payload


def _fetch_attachments(message_id: str, token: str) -> list[dict[str, Any]]:
    """
    Fetch attachment content for a message from Microsoft Graph.

    Returns a list of dicts with keys: filename, mimeType, size, data_base64.
    data_base64 is URL-safe base64 (as expected by all action handlers).
    Only file attachments are returned; item attachments (embedded messages) are skipped.
    """
    import base64 as _base64

    # Do not use $select — @odata.type is a system annotation not valid in $select,
    # and omitting $select ensures contentBytes is always returned.
    url = f"{GRAPH_BASE}/me/messages/{message_id}/attachments"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if not resp.ok:
        logger.warning(
            "Could not fetch attachments for message '%s' (HTTP %d): %s",
            message_id, resp.status_code, resp.text[:200],
        )
        return []

    attachments: list[dict[str, Any]] = []
    for att in resp.json().get("value", []):
        # Skip non-file attachments (embedded emails, calendar events, reference attachments)
        odata_type = att.get("@odata.type", "")
        if odata_type and "fileAttachment" not in odata_type:
            logger.debug("Skipping non-file attachment type '%s'", odata_type)
            continue

        content_bytes = att.get("contentBytes") or ""
        if not content_bytes:
            # Attachment > 3 MB: contentBytes is omitted by Graph — skip for now.
            logger.warning(
                "Attachment '%s' on message '%s' has no contentBytes (may be > 3 MB — not supported yet).",
                att.get("name"), message_id,
            )
            continue

        # Graph API returns standard base64 (+/); action handlers expect urlsafe (-_).
        try:
            raw = _base64.b64decode(content_bytes)
            data_b64 = _base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        except Exception as exc:
            logger.warning("Could not decode attachment '%s': %s", att.get("name"), exc)
            continue

        attachments.append({
            "filename": att.get("name") or "attachment",
            "mimeType": att.get("contentType") or "application/octet-stream",
            "size": att.get("size") or len(raw),
            "data_base64": data_b64,
        })

    logger.debug("Fetched %d attachment(s) for message '%s'", len(attachments), message_id)
    return attachments


def sync_outlook_connection(conn_id: str) -> dict[str, Any]:
    """Fetch and process new messages for one authenticated Outlook connection."""
    _get_outlook_connection(conn_id)
    conn_type, fields = _load_connection_fields(conn_id)
    if conn_type not in _OUTLOOK_TYPES:
        raise ValueError(f"Connection '{conn_id}' is not an outlook connection.")

    token, fields = _get_access_token(conn_id, fields, conn_type)

    last_sync_dt = _parse_iso_datetime(fields.get("_outlook_last_sync"))
    if not last_sync_dt:
        # First run: look back a small window to avoid pulling whole mailbox.
        last_sync_dt = datetime.now(timezone.utc) - timedelta(minutes=15)

    params = {
        "$top": "50",
        "$orderby": "receivedDateTime asc",
        "$select": "id,internetMessageId,subject,receivedDateTime,from,toRecipients,body,hasAttachments",
    }
    params["$filter"] = f"receivedDateTime gt {_to_graph_time(last_sync_dt)}"

    url = f"{GRAPH_BASE}/me/mailFolders/inbox/messages"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
    if not resp.ok:
        if resp.status_code == 403:
            raise RuntimeError(
                "Outlook Graph access denied (403). Re-authenticate this Outlook connection and ensure the Azure app has delegated Mail.Read permission."
            )
        raise RuntimeError(f"Outlook Graph query failed ({resp.status_code}): {resp.text[:300]}")

    headers = {"Authorization": f"Bearer {token}"}
    resp_data = resp.json()
    messages: list[dict] = list(resp_data.get("value") or [])
    next_link: str | None = resp_data.get("@odata.nextLink")
    while next_link:
        page = requests.get(next_link, headers=headers, timeout=30)
        if not page.ok:
            logger.warning("Outlook pagination failed (%s) for '%s'; processing %d so far.", page.status_code, conn_id, len(messages))
            break
        page_data = page.json()
        messages.extend(page_data.get("value") or [])
        next_link = page_data.get("@odata.nextLink")

    processed = 0
    max_seen_dt = last_sync_dt

    for message in messages:
        received_dt = _parse_iso_datetime(message.get("receivedDateTime"))
        if received_dt and received_dt > max_seen_dt:
            max_seen_dt = received_dt

        email_payload = _extract_email_payload(message, conn_id)
        if message.get("hasAttachments"):
            email_payload["attachments"] = _fetch_attachments(
                message["id"], token
            )
        process_inbound_email(email_payload)
        processed += 1

    # Move pointer 1 second forward to avoid replaying same timestamp on next poll.
    fields["_outlook_last_sync"] = _to_graph_time(max_seen_dt + timedelta(seconds=1))
    _save_connection_fields(conn_id, fields)

    return {"connection_id": conn_id, "processed": processed, "last_sync": fields["_outlook_last_sync"]}


def sync_all_outlook_connections() -> dict[str, Any]:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        rows = [
            c.id for c in crud.get_connections(db)
            if c.direction == "inbound"
            and c.type in _OUTLOOK_TYPES
            and is_enabled_flag((c.fields or {}).get("enabled", True))
        ]

    ok: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for conn_id in rows:
        try:
            ok.append(sync_outlook_connection(conn_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Outlook sync failed for '%s': %s", conn_id, exc)
            errors.append({"connection_id": conn_id, "error": str(exc)})

    return {"processed_connections": len(ok), "errors": errors, "details": ok}


def _auth_thread(conn_id: str, client_id: str, flow: dict[str, Any]) -> None:
    try:
        _type, fields = _load_connection_fields(conn_id)
        app, cache = _build_msal_app(client_id, fields.get("_msal_cache"), _get_authority(fields, _type))
        result = app.acquire_token_by_device_flow(flow)

        if "access_token" in result:
            fields["_msal_cache"] = cache.serialize()
            if result.get("id_token_claims", {}).get("preferred_username"):
                fields["_outlook_user"] = result["id_token_claims"]["preferred_username"]
            _save_connection_fields(conn_id, fields)
            try:
                from app.services.outlook_webhook_service import ensure_outlook_subscription

                ensure_outlook_subscription(conn_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Outlook webhook subscription setup failed for '%s': %s", conn_id, exc)
            with _state_lock:
                _auth_state[conn_id] = {"status": "success", "message": "Outlook authentication successful."}
            return

        error = result.get("error_description") or result.get("error") or "Unknown error"
        with _state_lock:
            _auth_state[conn_id] = {"status": "error", "message": error}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Outlook auth thread failed for %s: %s", conn_id, exc)
        with _state_lock:
            _auth_state[conn_id] = {"status": "error", "message": str(exc)}


def start_outlook_auth(conn_id: str, client_id_override: str = "") -> dict[str, Any]:
    _get_outlook_connection(conn_id)
    _type, fields = _load_connection_fields(conn_id)

    client_id = (client_id_override or fields.get("client_id") or get_settings().outlook_client_id or "").strip()
    if not client_id:
        raise ValueError("No Outlook client_id configured. Set OUTLOOK_CLIENT_ID or connection field client_id.")

    with _state_lock:
        existing = _auth_state.get(conn_id, {})
        if existing.get("status") == "pending":
            return existing

    app, _cache = _build_msal_app(client_id, fields.get("_msal_cache"), _get_authority(fields, _type))

    # Fast-path: valid token already exists.
    accounts = app.get_accounts()
    if accounts:
        silent = app.acquire_token_silent(SCOPES, account=accounts[0])
        if silent and "access_token" in silent:
            with _state_lock:
                _auth_state[conn_id] = {"status": "success", "message": "Already authenticated."}
            return _auth_state[conn_id]

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        error_codes = flow.get("error_codes") or []
        if 9002346 in error_codes or "AADSTS9002346" in json.dumps(flow):
            raise RuntimeError(
                "Azure app registration mismatch. "
                "Your Azure app supports personal Microsoft accounts only (/consumers) "
                f"but the '{_type}' connection type requires work/school account support. "
                "Fix: In the Azure portal → App registrations → Authentication → "
                "change 'Supported account types' to 'Accounts in any organizational "
                "directory and personal Microsoft accounts' (Multitenant + personal). "
                "Alternatively, use the 'outlook' connection type for personal accounts."
            )
        raise RuntimeError(f"Failed to initiate Outlook auth flow: {json.dumps(flow)}")

    expires_at = datetime.fromtimestamp(time.time() + flow.get("expires_in", 900), tz=timezone.utc).isoformat()
    state = {
        "status": "pending",
        "user_code": flow["user_code"],
        "verification_url": flow.get("verification_uri") or "https://microsoft.com/devicelogin",
        "message": flow.get("message") or "Complete sign-in in browser using the code shown.",
        "expires_at": expires_at,
    }
    with _state_lock:
        _auth_state[conn_id] = state

    t = threading.Thread(target=_auth_thread, args=(conn_id, client_id, flow), daemon=True)
    t.start()
    return state


def get_auth_status(conn_id: str) -> dict[str, Any]:
    with _state_lock:
        return _auth_state.get(conn_id, {"status": "idle"})


def get_outlook_token_status(conn_id: str) -> dict[str, Any]:
    """Check the persisted MSAL token without making any Graph network calls.

    Returns a dict with at minimum ``token_status`` ("valid", "expired", or "missing").
    On a valid token also includes ``token_expiry`` (ISO-8601) and ``outlook_user``.
    """
    try:
        _conn_type, fields = _load_connection_fields(conn_id)
        if not fields.get("_msal_cache"):
            return {"token_status": "missing"}

        client_id = (fields.get("client_id") or get_settings().outlook_client_id or "").strip()
        if not client_id:
            return {"token_status": "missing"}

        app, _cache = _build_msal_app(client_id, fields["_msal_cache"], _get_authority(fields, _conn_type))
        accounts = app.get_accounts()
        if not accounts:
            return {"token_status": "missing"}

        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result or "access_token" not in result:
            return {"token_status": "expired"}

        expires_in = int(result.get("expires_in") or 3600)
        expiry_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        outlook_user = (
            fields.get("_outlook_user")
            or (result.get("id_token_claims") or {}).get("preferred_username")
            or ""
        )
        return {
            "token_status": "valid",
            "token_expiry": expiry_dt.isoformat(),
            "outlook_user": outlook_user,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Outlook token status check failed for '%s': %s", conn_id, exc)
        return {"token_status": "error"}


def clear_auth_status(conn_id: str) -> dict[str, bool]:
    with _state_lock:
        _auth_state.pop(conn_id, None)
    return {"cleared": True}


def reset_outlook_auth(conn_id: str) -> dict[str, bool]:
    """Clear the MSAL token cache for a connection, forcing re-authentication."""
    with _state_lock:
        _auth_state.pop(conn_id, None)
    _conn_type, fields = _load_connection_fields(conn_id)
    fields.pop("_msal_cache", None)
    _save_connection_fields(conn_id, fields)
    return {"reset": True}
