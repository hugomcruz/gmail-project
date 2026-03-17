"""Gmail API service — authentication, watch management, and history fetching."""

import json
import logging
import os
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from notif_receiver.config import get_settings

logger = logging.getLogger(__name__)

# Scopes required by this service
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _get_credentials() -> Credentials:
    """Load or refresh OAuth2 credentials.

    Resolution order:
    1. Database (primary — survives container restarts)
    2. Local file (migration fallback — loaded once then migrated to DB)

    After every refresh the updated token is written back to the database.
    """
    from notif_receiver.services.token_store import load_token, save_token

    settings = get_settings()
    creds: Credentials | None = None

    # 1. Try database first
    token_json = load_token()
    if token_json:
        creds = Credentials.from_authorized_user_info(
            json.loads(token_json), SCOPES
        )
    elif os.path.exists(settings.gmail_token_file):
        # Migration: file exists but nothing in DB yet — load and migrate
        creds = Credentials.from_authorized_user_file(settings.gmail_token_file, SCOPES)
        logger.info(
            "Migrating OAuth token from %s to database.",
            settings.gmail_token_file,
        )
        save_token(creds.to_json())

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            save_token(creds.to_json())
        else:
            from notif_receiver.services.token_store import load_client_secret
            client_secret_json = load_client_secret()
            if not client_secret_json:
                raise RuntimeError(
                    "No OAuth2 client secret found. Set GMAIL_CLIENT_SECRET_JSON "
                    "or seed the database with the client_secret.json contents."
                )
            raise RuntimeError(
                "No valid OAuth token found in the database. "
                "Complete the OAuth flow by visiting /gmail/auth in the web UI."
            )

    return creds


def get_gmail_service():
    """Return an authenticated Gmail API resource."""
    return build("gmail", "v1", credentials=_get_credentials())


# ---------------------------------------------------------------------------
# Watch management
# ---------------------------------------------------------------------------

def start_watch(
    topic_name: str,
    label_ids: list[str] | None = None,
    label_filter_action: str = "include",
) -> dict[str, Any]:
    """
    Call Gmail's users.watch to subscribe to push notifications.

    Args:
        topic_name: Full Pub/Sub topic name, e.g.
                    'projects/my-project/topics/gmail-notifications'.
        label_ids:  Gmail label IDs to filter on (default: INBOX).
        label_filter_action: 'include' or 'exclude'.

    Returns:
        Dict with 'historyId' and 'expiration' fields.
    """
    if label_ids is None:
        label_ids = ["INBOX"]

    settings = get_settings()
    service = get_gmail_service()

    body: dict[str, Any] = {
        "topicName": topic_name,
        "labelIds": label_ids,
        "labelFilterAction": label_filter_action,
    }

    try:
        response = (
            service.users()
            .watch(userId=settings.gmail_user_id, body=body)
            .execute()
        )
        logger.info("Gmail watch started: historyId=%s", response.get("historyId"))
        return response
    except HttpError as exc:
        logger.error("Failed to start Gmail watch: %s", exc)
        raise


def stop_watch() -> None:
    """Stop all Gmail push notifications for the authenticated user."""
    settings = get_settings()
    service = get_gmail_service()

    try:
        service.users().stop(userId=settings.gmail_user_id).execute()
        logger.info("Gmail watch stopped.")
    except HttpError as exc:
        logger.error("Failed to stop Gmail watch: %s", exc)
        raise


# ---------------------------------------------------------------------------
# History / message fetching
# ---------------------------------------------------------------------------

def list_history(start_history_id: str) -> list[str]:
    """
    Retrieve new message IDs since *start_history_id*, filtered by the
    configured watched labels (gmail_watched_labels in .env).

    Queries the history API once per watched label (the API only accepts
    one labelId per call) and deduplicates the results.

    Returns a deduplicated list of new message IDs.
    """
    settings = get_settings()
    service = get_gmail_service()
    message_ids: set[str] = set()

    for label in settings.watched_labels:
        page_token: str | None = None
        try:
            while True:
                kwargs: dict[str, Any] = {
                    "userId": settings.gmail_user_id,
                    "startHistoryId": start_history_id,
                    "historyTypes": ["messageAdded"],
                    "labelId": label,
                }
                if page_token:
                    kwargs["pageToken"] = page_token

                result = service.users().history().list(**kwargs).execute()
                for record in result.get("history", []):
                    for msg_added in record.get("messagesAdded", []):
                        message_ids.add(msg_added["message"]["id"])
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as exc:
            if exc.resp.status == 404:
                logger.warning("historyId %s not found for label '%s'; re-sync required.", start_history_id, label)
            else:
                logger.error("Error fetching Gmail history for label '%s': %s", label, exc)
                raise

    logger.debug("list_history since %s — found %d message(s) across labels: %s",
                 start_history_id, len(message_ids), settings.watched_labels)
    return list(message_ids)


def list_labels() -> list[dict[str, Any]]:
    """Return all Gmail labels (id + name + type) for the authenticated user."""
    settings = get_settings()
    service = get_gmail_service()
    result = service.users().labels().list(userId=settings.gmail_user_id).execute()
    return sorted(result.get("labels", []), key=lambda l: l.get("name", ""))


def get_message(message_id: str) -> dict[str, Any]:
    """Fetch a single Gmail message by ID (full format)."""
    settings = get_settings()
    service = get_gmail_service()

    try:
        return (
            service.users()
            .messages()
            .get(userId=settings.gmail_user_id, id=message_id, format="full")
            .execute()
        )
    except HttpError as exc:
        logger.error("Failed to fetch message %s: %s", message_id, exc)
        raise


def extract_body(
    payload: dict[str, Any],
    *,
    service: Any | None = None,
    user_id: str = "me",
    message_id: str = "",
) -> str:
    """
    Recursively extract the plaintext (or HTML fallback) body from a
    Gmail message payload, handling simple and multipart messages.

    Also fetches oversized body parts that Gmail stores via attachmentId
    rather than inline base64 data.
    """
    import base64

    mime_type = payload.get("mimeType", "")
    body      = payload.get("body", {})
    body_data: str = body.get("data", "") or ""
    attach_id: str = body.get("attachmentId", "") or ""

    # Fetch body stored as a remote attachment (Gmail does this for large parts)
    if not body_data and attach_id and service and message_id:
        try:
            att = (
                service.users()
                .messages()
                .attachments()
                .get(userId=user_id, messageId=message_id, id=attach_id)
                .execute()
            )
            body_data = att.get("data", "") or ""
        except Exception as exc:
            logger.warning("Could not fetch body attachment %s: %s", attach_id, exc)

    # Decode inline / fetched data — only return if non-empty after stripping
    if body_data:
        try:
            text = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace").strip()
            if text:
                return text
        except Exception:
            pass

    # Multipart: prefer text/plain; fall back to text/html; recurse into sub-multiparts
    # but do NOT let a sub-multipart overwrite an already-found plain-text value.
    parts = payload.get("parts", [])
    plain: str = ""
    html:  str = ""

    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            result = extract_body(part, service=service, user_id=user_id, message_id=message_id)
            if result and result != "(no body)":
                plain = result
        elif part_mime == "text/html":
            if not html:
                result = extract_body(part, service=service, user_id=user_id, message_id=message_id)
                if result and result != "(no body)":
                    html = result
        elif part_mime.startswith("multipart/"):
            # Only use the recursed result if we haven't found plain text yet
            if not plain:
                nested = extract_body(part, service=service, user_id=user_id, message_id=message_id)
                if nested and nested != "(no body)":
                    plain = nested

    return plain or html or "(no body)"


def _extract_html_body(
    payload: dict[str, Any],
    *,
    service: Any | None = None,
    user_id: str = "me",
    message_id: str = "",
) -> str:
    """
    Recursively search the full MIME tree for the first text/html part
    and return its decoded content (handles arbitrary nesting depth).
    """
    if payload.get("mimeType") == "text/html":
        result = extract_body(payload, service=service, user_id=user_id, message_id=message_id)
        return result if result != "(no body)" else ""

    for part in payload.get("parts", []):
        result = _extract_html_body(part, service=service, user_id=user_id, message_id=message_id)
        if result:
            return result

    return ""


def _extract_attachments(
    parts: list[dict[str, Any]],
    service: Any,
    user_id: str,
    message_id: str,
) -> list[dict[str, Any]]:
    """
    Recursively walk MIME parts and return a list of attachment dicts.
    Each dict contains: filename, mimeType, size, data_base64 (base64url string).
    """
    attachments: list[dict[str, Any]] = []

    for part in parts:
        mime = part.get("mimeType", "")
        sub_parts = part.get("parts", [])

        if sub_parts:
            attachments.extend(_extract_attachments(sub_parts, service, user_id, message_id))
            continue

        # Skip plain text and HTML body parts
        if mime in ("text/plain", "text/html"):
            continue

        filename = part.get("filename") or f"attachment_{len(attachments) + 1}"
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")
        size = body.get("size", 0)
        data: str = ""

        if attachment_id:
            try:
                att = (
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId=user_id, messageId=message_id, id=attachment_id)
                    .execute()
                )
                data = att.get("data", "")
            except Exception as exc:
                logger.error("Failed to fetch attachment '%s': %s", filename, exc)
        else:
            data = body.get("data", "")

        attachments.append(
            {
                "filename": filename,
                "mimeType": mime,
                "size": size,
                "data_base64": data,
            }
        )

    return attachments


def get_full_email(message_id: str) -> dict[str, Any] | None:
    """
    Fetch a Gmail message and return a fully structured dict including:
    headers (from, to, cc, bcc, subject, date), plain and HTML body,
    and all attachments as base64url-encoded strings.

    Returns None if the message should be skipped (DRAFT, SENT, or not
    matching any watched label).
    """
    settings = get_settings()
    service = get_gmail_service()
    user_id = settings.gmail_user_id

    try:
        msg = (
            service.users()
            .messages()
            .get(userId=user_id, id=message_id, format="full")
            .execute()
        )
    except HttpError as exc:
        logger.error("Failed to fetch message %s: %s", message_id, exc)
        raise

    label_ids = msg.get("labelIds", [])

    # Always skip drafts and sent mail
    if "DRAFT" in label_ids or "SENT" in label_ids:
        logger.info("Skipping message %s — DRAFT or SENT (labels: %s)", message_id, label_ids)
        return None

    # Skip if the message doesn't carry any of the watched labels
    if not any(lbl in label_ids for lbl in settings.watched_labels):
        logger.info("Skipping message %s — labels %s not in watched labels %s",
                    message_id, label_ids, settings.watched_labels)
        return None

    payload = msg.get("payload", {})
    headers = {
        h["name"].lower(): h["value"]
        for h in payload.get("headers", [])
    }

    parts = payload.get("parts", [])
    body_plain = extract_body(payload, service=service, user_id=user_id, message_id=message_id)
    body_html = _extract_html_body(payload, service=service, user_id=user_id, message_id=message_id)

    attachments = _extract_attachments(parts, service, user_id, message_id)

    email: dict[str, Any] = {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "labelIds": msg.get("labelIds", []),
        "snippet": msg.get("snippet", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "bcc": headers.get("bcc", ""),
        "reply_to": headers.get("reply-to", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date": headers.get("date", ""),
        "body_plain": body_plain,
        "body_html": body_html,
        "attachments": attachments,
    }

    att_names = ", ".join(
        f"{a['filename']} ({a['size']} B)" for a in attachments
    ) or "none"

    # Single-line summary for INFO; full details only in DEBUG
    logger.info(
        "New email | From: %s | Subject: %s | Date: %s | Attachments: %s",
        email["from"], email["subject"], email["date"], att_names,
    )
    logger.debug(
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  New email received\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  From    : %s\n"
        "  To      : %s\n"
        "  Subject : %s\n"
        "  Date    : %s\n"
        "  ID      : %s\n"
        "  Attach  : %s\n"
        "──────────────────────────────────────────────────\n"
        "%s\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        email["from"], email["to"], email["subject"],
        email["date"], email["id"], att_names, body_plain,
    )

    return email


# ---------------------------------------------------------------------------
# OAuth2 web flow (for UI-driven re-authorization)
# ---------------------------------------------------------------------------

# In-memory map of state -> Flow for pending OAuth flows.
# Only one flow should be active at a time in normal operation.
_pending_flows: dict[str, Any] = {}


def get_token_status() -> dict[str, Any]:
    """Return a dict describing the current state of the stored OAuth token."""
    from notif_receiver.services.token_store import load_token

    settings = get_settings()

    # Prefer database; fall back to file for local/dev environments
    token_json = load_token()
    if token_json:
        try:
            creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        except Exception as exc:
            return {"token_status": "error", "token_expiry": None, "scopes": [], "message": str(exc)}
    elif os.path.exists(settings.gmail_token_file):
        try:
            creds = Credentials.from_authorized_user_file(settings.gmail_token_file, SCOPES)
        except Exception as exc:
            return {"token_status": "error", "token_expiry": None, "scopes": [], "message": str(exc)}
    else:
        return {"token_status": "missing", "token_expiry": None, "scopes": []}

    expiry = creds.expiry.isoformat() if creds.expiry else None
    scopes = list(creds.scopes or [])
    if creds.valid:
        return {"token_status": "valid", "token_expiry": expiry, "scopes": scopes}
    if creds.expired and creds.refresh_token:
        return {"token_status": "expired", "token_expiry": expiry, "scopes": scopes}
    return {"token_status": "invalid", "token_expiry": expiry, "scopes": scopes}


def create_oauth_flow(redirect_uri: str) -> tuple[str, str]:
    """
    Build an OAuth2 authorization URL for the web-redirect flow.

    Returns:
        (auth_url, state) — the URL to redirect the user to, and the
        CSRF-protection state value stored for callback verification.
    """
    from notif_receiver.services.token_store import load_client_secret
    client_secret_json = load_client_secret()
    if not client_secret_json:
        raise RuntimeError(
            "No OAuth2 client secret found. Set GMAIL_CLIENT_SECRET_JSON "
            "or seed the database with the client_secret.json contents."
        )
    flow = Flow.from_client_config(
        json.loads(client_secret_json),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    _pending_flows[state] = flow
    return auth_url, state


def complete_oauth_flow(code: str, state: str) -> None:
    """
    Exchange the authorization code for credentials and persist the token.

    Raises ValueError if the state does not match any pending flow.
    """
    flow = _pending_flows.pop(state, None)
    if flow is None:
        raise ValueError("Invalid or expired OAuth state parameter.")

    # Allow HTTP redirect_uri in local/dev environments
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow.fetch_token(code=code)
    creds = flow.credentials

    from notif_receiver.services.token_store import save_token
    save_token(creds.to_json())
    logger.info("Gmail OAuth token saved to database via web OAuth flow.")

    # Also write to file so local dev / manual inspection still works
    settings = get_settings()
    try:
        with open(settings.gmail_token_file, "w") as token_file:
            token_file.write(creds.to_json())
    except OSError:
        pass  # file path may not be writable in container — that's fine
