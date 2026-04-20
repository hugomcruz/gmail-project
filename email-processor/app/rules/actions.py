"""
Action executors for the rules engine.

Each action in rules.yaml has:
  type        action type (upload_to_s3 | upload_to_onedrive | create_jira_task)
  connection  ID referencing a connection in connections.yaml
  config      optional action-level overrides (merged on top of connection config)

The `execute(action, email, registry)` function resolves the connection,
merges configs, and dispatches to the appropriate handler.
"""

import logging
from typing import Any, TYPE_CHECKING
from datetime import datetime

from app.config import get_settings

if TYPE_CHECKING:
    from app.rules.connections import ConnectionRegistry

logger = logging.getLogger(__name__)


def execute(
    action: dict[str, Any],
    email: dict[str, Any],
    registry: "ConnectionRegistry",
) -> dict[str, Any]:
    """
    Resolve the connection, merge configs, and dispatch to the handler.
    Returns a result dict with at minimum {"action": type, "status": "ok"|"error"}.
    """
    atype = action.get("type", "")
    connection_id = action.get("connection", "")
    action_config = action.get("config", {}) or {}

    # Resolve connection from registry
    try:
        conn = registry.get(connection_id)
    except KeyError as exc:
        logger.error("Action '%s': %s", atype, exc)
        return {"action": atype, "status": "error", "error": str(exc)}

    # Merge: connection config is the base, action config overrides
    merged = {**conn, **action_config}

    try:
        handler = _HANDLERS[atype]
    except KeyError:
        logger.warning("Unknown action type '%s' — skipping", atype)
        return {"action": atype, "status": "skipped", "reason": "unknown action type"}

    try:
        result = handler(merged, email)
        return {"action": atype, "connection": connection_id, "status": "ok", **result}
    except Exception as exc:
        logger.exception("Action '%s' (connection='%s') failed: %s", atype, connection_id, exc)
        return {"action": atype, "connection": connection_id, "status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Template helper
# ---------------------------------------------------------------------------

def _render(template: str, email: dict[str, Any]) -> str:
    """Substitute {field} placeholders with email values."""
    now = datetime.utcnow()
    attachment_names = ", ".join(
        a.get("filename", "?") for a in email.get("attachments", [])
    ) or "none"

    context = {
        "id": email.get("id", ""),
        "from": email.get("from", ""),
        "to": email.get("to", ""),
        "subject": email.get("subject", ""),
        "date": email.get("date", ""),
        "snippet": email.get("snippet", ""),
        "body": email.get("body_plain", ""),
        "attachment_names": attachment_names,
        "attachment_count": str(len(email.get("attachments", []))),
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
    }
    try:
        return template.format_map(context)
    except KeyError as exc:
        logger.warning("Template placeholder %s not found — left as-is", exc)
        return template


# ---------------------------------------------------------------------------
# S3 action
# ---------------------------------------------------------------------------

def _upload_to_s3(config: dict[str, Any], email: dict[str, Any]) -> dict[str, Any]:
    """
    Upload every attachment to S3 using the resolved connection config.

    Config keys (from connection + action overrides):
      bucket            required  S3 bucket
      region            optional  AWS region
      access_key_id     optional  Explicit credentials
      secret_access_key optional
      endpoint_url      optional  Custom endpoint for S3-compatible providers (Scaleway, MinIO…)
      storage_class     optional  e.g. STANDARD, STANDARD_IA, GLACIER
      prefix            optional  Key prefix; supports {year}/{month}/{day} placeholders
    """
    import base64
    from app.services.s3_service import upload_bytes

    attachments = email.get("attachments", [])
    if not attachments:
        return {"uploaded": []}

    prefix = _render(config.get("prefix", ""), email)

    uploaded = []
    for att in attachments:
        filename = att.get("filename", "attachment")
        data_b64 = att.get("data_base64", "")
        mime_type = att.get("mimeType", "application/octet-stream")

        try:
            raw = base64.urlsafe_b64decode(data_b64 + "==")
        except Exception as exc:
            logger.error("Could not decode attachment '%s': %s", filename, exc)
            continue

        s3_key = f"{prefix}{filename}" if prefix else filename
        s3_url = upload_bytes(
            data=raw,
            key=s3_key,
            content_type=mime_type,
            bucket=config["bucket"],
            region=config.get("region", "us-east-1"),
            access_key_id=config.get("access_key_id") or None,
            secret_access_key=config.get("secret_access_key") or None,
            endpoint_url=config.get("endpoint_url") or None,
            storage_class=config.get("storage_class") or None,
        )
        logger.info("S3 uploaded '%s' → %s", filename, s3_url)
        uploaded.append({"filename": filename, "s3_url": s3_url})

    return {"uploaded": uploaded}


# ---------------------------------------------------------------------------
# OneDrive action
# ---------------------------------------------------------------------------

def _upload_to_onedrive(config: dict[str, Any], email: dict[str, Any]) -> dict[str, Any]:
    """
    Upload every attachment to OneDrive using the resolved connection config.

    Config keys (from connection + action overrides):
      client_id    required  Azure app client ID
      folder       optional  Destination folder; supports {year}/{month}/{day}
    """
    import base64
    from app.services.onedrive_service import upload_bytes

    attachments = email.get("attachments", [])
    if not attachments:
        return {"uploaded": []}

    folder = _render(config.get("folder", ""), email)
    conn_id = config.get("id", "")
    # Always read the token cache fresh from DB — the in-memory registry may be
    # stale if auth completed after the last registry reload.
    cache_data: str | None = _load_msal_cache_from_db(conn_id) or config.get("_msal_cache") or None
    # client_id may be in the connection config or fall back to server settings
    client_id: str = config.get("client_id") or get_settings().onedrive_client_id
    if not client_id:
        raise ValueError(
            "No OneDrive client_id available. Set ONEDRIVE_CLIENT_ID in the server environment."
        )

    uploaded = []
    for att in attachments:
        filename = att.get("filename", "attachment")
        data_b64 = att.get("data_base64", "")
        mime_type = att.get("mimeType", "application/octet-stream")

        try:
            raw = base64.urlsafe_b64decode(data_b64 + "==")
        except Exception as exc:
            logger.error("Could not decode attachment '%s': %s", filename, exc)
            continue

        web_url, updated_cache = upload_bytes(
            data=raw,
            remote_path=filename,
            content_type=mime_type,
            folder=folder or None,
            client_id=client_id,
            token_cache_data=cache_data,
        )
        # If token was silently refreshed, persist the new cache to DB
        if updated_cache:
            cache_data = updated_cache
            _save_onedrive_cache(conn_id, updated_cache)

        logger.info("OneDrive uploaded '%s' → %s", filename, web_url)
        uploaded.append({"filename": filename, "onedrive_url": web_url})

    return {"uploaded": uploaded}


def _save_onedrive_cache(conn_id: str, cache_data: str) -> None:
    """Persist an updated MSAL token cache string into the connection's DB record."""
    if not conn_id:
        return
    try:
        from app.db.database import get_session_factory
        from app.db import crud
        SessionLocal = get_session_factory()
        with SessionLocal() as db:
            conn = crud.get_connection(db, conn_id)
            if conn:
                fields = dict(conn.fields or {})
                fields["_msal_cache"] = cache_data
                crud.update_connection(db, conn_id, conn.direction, conn.type, fields)
        # Reload registry so the refreshed cache is available for subsequent runs
        from app.state import engine
        engine.reload_connections()
    except Exception as exc:
        logger.warning("Could not save OneDrive token cache for '%s': %s", conn_id, exc)


def _load_msal_cache_from_db(conn_id: str) -> str | None:
    """Read _msal_cache fresh from the DB, bypassing the in-memory registry.

    The in-memory registry may be stale if auth completed after the last reload.
    Always reading from DB guarantees we use the token that was actually saved.
    """
    if not conn_id:
        return None
    try:
        from app.db.database import get_session_factory
        from app.db import crud
        SessionLocal = get_session_factory()
        with SessionLocal() as db:
            conn = crud.get_connection(db, conn_id)
            if conn:
                return (conn.fields or {}).get("_msal_cache") or None
    except Exception as exc:
        logger.warning("Could not load MSAL cache for '%s' from DB: %s", conn_id, exc)
    return None


# ---------------------------------------------------------------------------
# OneDrive 365 / SharePoint action
# ---------------------------------------------------------------------------

def _upload_to_onedrive365(config: dict[str, Any], email: dict[str, Any]) -> dict[str, Any]:
    """
    Upload every attachment to OneDrive for Business or SharePoint.

    Config keys (from connection + action overrides):
      folder     optional  Destination folder; supports {year}/{month}/{day}
      site_url   optional  SharePoint site URL (leave blank for OneDrive for Business)
    """
    import base64
    from app.services.onedrive365_service import upload_bytes

    attachments = email.get("attachments", [])
    if not attachments:
        logger.info(
            "upload_to_onedrive365 (conn='%s'): email id=%s has no attachments — nothing to upload.",
            config.get("id", ""), email.get("id", ""),
        )
        return {"uploaded": [], "skipped_reason": "no attachments"}

    folder = _render(config.get("folder", ""), email)
    conn_id = config.get("id", "")
    # Always read the token cache fresh from DB — the in-memory registry may be
    # stale if auth completed after the last registry reload.
    cache_data: str | None = _load_msal_cache_from_db(conn_id) or config.get("_msal_cache") or None
    site_url: str = config.get("site_url") or ""
    tenant_id: str = config.get("tenant_id") or ""

    client_id: str = config.get("client_id") or get_settings().outlook_client_id or get_settings().onedrive_client_id
    if not client_id:
        raise ValueError(
            "No OneDrive 365 client_id available. Set AZURE_CLIENT_ID in the server environment."
        )

    if not cache_data:
        raise RuntimeError(
            f"OneDrive 365 connection '{conn_id}' has no stored token. "
            "Authenticate the connection via the Connections page first."
        )

    logger.info(
        "upload_to_onedrive365 (conn='%s'): uploading %d attachment(s) from email id=%s",
        conn_id, len(attachments), email.get("id", ""),
    )

    uploaded = []
    for att in attachments:
        filename = att.get("filename", "attachment")
        data_b64 = att.get("data_base64", "")
        mime_type = att.get("mimeType", "application/octet-stream")

        try:
            raw = base64.urlsafe_b64decode(data_b64 + "==")
        except Exception as exc:
            logger.error("Could not decode attachment '%s': %s", filename, exc)
            continue

        web_url, updated_cache = upload_bytes(
            data=raw,
            remote_path=filename,
            content_type=mime_type,
            folder=folder or None,
            client_id=client_id,
            tenant_id=tenant_id,
            site_url=site_url,
            token_cache_data=cache_data,
        )
        if updated_cache:
            cache_data = updated_cache
            _save_onedrive_cache(conn_id, updated_cache)

        logger.info("OneDrive 365 uploaded '%s' → %s", filename, web_url)
        uploaded.append({"filename": filename, "onedrive_url": web_url})

    return {"uploaded": uploaded}


# ---------------------------------------------------------------------------
# JIRA action
# ---------------------------------------------------------------------------

def _create_jira_task(config: dict[str, Any], email: dict[str, Any]) -> dict[str, Any]:
    """
    Create a JIRA issue using the resolved connection config.
    All email attachments are uploaded to the issue after creation.

    Config keys (from connection + action overrides):
      url                  required  JIRA base URL
      user                 required  JIRA username / email
      token                required  API token
      project              optional  JIRA project key (falls back to default_project)
      issue_type           optional  Issue type (falls back to default_issue_type)
      summary_template     optional  Supports {field} placeholders
      description_template optional  Supports {field} placeholders
      labels               optional  List of label strings
      priority             optional  Highest/High/Medium/Low/Lowest
      attach_files         optional  true (default) | false — set false to skip uploads
    """
    import base64
    from app.services.jira_service import create_issue

    summary = _render(
        config.get("summary_template", "Email: {subject}"),
        email,
    )
    description = _render(
        config.get("description_template", "From: {from}\nDate: {date}\n\n{body}"),
        email,
    )
    project = config.get("project") or config.get("default_project", "")
    issue_type = config.get("issue_type") or config.get("default_issue_type", "Task")

    # Decode email attachments unless explicitly disabled
    attachments = []
    if config.get("attach_files", True):
        for att in email.get("attachments", []):
            data_b64 = att.get("data_base64", "")
            try:
                raw = base64.urlsafe_b64decode(data_b64 + "==")
            except Exception as exc:
                logger.error("Could not decode attachment '%s': %s", att.get("filename"), exc)
                continue
            attachments.append({
                "filename": att.get("filename", "attachment"),
                "data": raw,
                "mime_type": att.get("mimeType", "application/octet-stream"),
            })

        # Attach the email body as plain-text, HTML, and PDF files
        body_plain = email.get("body_plain", "")
        body_html = email.get("body_html", "")
        if body_plain:
            attachments.append({
                "filename": "email-content.txt",
                "data": body_plain.encode("utf-8"),
                "mime_type": "text/plain",
            })
        if body_html:
            attachments.append({
                "filename": "email-content.html",
                "data": body_html.encode("utf-8"),
                "mime_type": "text/html",
            })
            try:
                import weasyprint
                pdf_bytes = weasyprint.HTML(string=body_html).write_pdf()
                attachments.append({
                    "filename": "email-content.pdf",
                    "data": pdf_bytes,
                    "mime_type": "application/pdf",
                })
            except Exception as exc:
                logger.warning("Could not convert email body to PDF: %s", exc)

    issue_key = create_issue(
        jira_url=config["url"],
        jira_user=config["user"],
        jira_token=config["token"],
        project=project,
        issue_type=issue_type,
        summary=summary,
        description=description,
        labels=config.get("labels", []),
        priority=config.get("priority", "Medium"),
        attachments=attachments or None,
    )
    logger.info(
        "Created JIRA issue %s for email id=%s with %d attachment(s)",
        issue_key, email.get("id"), len(attachments),
    )
    return {"jira_issue": issue_key, "attachments_uploaded": len(attachments)}


# ---------------------------------------------------------------------------
# Forward action
# ---------------------------------------------------------------------------

def _forward_email(config: dict[str, Any], email: dict[str, Any]) -> dict[str, Any]:
    """
    Forward an email via Mailgun using the resolved connection config.

    Config keys (from connection + action overrides):
      api_key          required  Mailgun private API key
      domain           required  Mailgun sending domain (e.g. berzuk.com)
      sender_address   required  Fixed From address (e.g. heat@berzuk.com)
      to               required  Recipient address or list of addresses
      subject_prefix   optional  Prefixed to the original subject (e.g. "Fwd: ")
    """
    from app.services.mailgun_service import forward_email

    to = config.get("to", [])
    if isinstance(to, str):
        to = [to]

    if not to:
        raise ValueError("forward_email action requires 'to' in config.")

    message_id = forward_email(
        api_key=config["api_key"],
        domain=config["domain"],
        sender_address=config["sender_address"],
        to=to,
        original_email=email,
        subject_prefix=config.get("subject_prefix", ""),
        api_base=config.get("api_base", "") or "https://api.mailgun.net/v3",
    )
    logger.info(
        "Forwarded email id=%s to %s — Mailgun id: %s",
        email.get("id"), to, message_id,
    )
    return {"forwarded_to": to, "mailgun_id": message_id}


_HANDLERS = {
    "upload_to_s3": _upload_to_s3,
    "upload_to_onedrive": _upload_to_onedrive,
    "upload_to_onedrive365": _upload_to_onedrive365,
    "create_jira_task": _create_jira_task,
    "forward_email": _forward_email,
}
