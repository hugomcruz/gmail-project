"""Gmail watch management endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

from notif_receiver.config import get_settings
from notif_receiver.models import GmailWatchRequest, GmailWatchResponse
from notif_receiver.services.gmail_service import (
    start_watch,
    stop_watch,
    list_labels,
    get_token_status,
    create_oauth_flow,
    complete_oauth_flow,
)
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gmail", tags=["Gmail"])


def _build_topic_name(override: str | None = None) -> str:
    """Return the full Pub/Sub topic name."""
    settings = get_settings()
    if override:
        return override
    return f"projects/{settings.google_cloud_project_id}/topics/{settings.pubsub_topic_name}"


@router.post(
    "/watch",
    summary="Start Gmail push notifications",
    response_model=GmailWatchResponse,
    status_code=status.HTTP_200_OK,
)
async def watch(body: GmailWatchRequest = GmailWatchRequest()) -> GmailWatchResponse:
    """
    Register a Pub/Sub watch on the authenticated Gmail account.

    The watch expires after ~7 days; call this endpoint periodically
    (e.g., with Cloud Scheduler) to keep it active.
    """
    topic = _build_topic_name(body.topic_name)
    try:
        response = start_watch(
            topic_name=topic,
            label_ids=body.label_ids,
            label_filter_action=body.label_filter_action,
        )
    except HttpError as exc:
        raise HTTPException(
            status_code=exc.resp.status,
            detail=str(exc),
        ) from exc

    return GmailWatchResponse(
        historyId=str(response["historyId"]),
        expiration=str(response["expiration"]),
    )


@router.get(
    "/labels",
    summary="List all Gmail labels",
    status_code=status.HTTP_200_OK,
)
async def get_labels() -> list[dict]:
    """
    Returns all Gmail labels for the authenticated user, including system
    labels (INBOX, SENT, DRAFT …) and any custom labels.

    Use the `id` field when setting GMAIL_WATCHED_LABELS in .env.
    """
    try:
        return list_labels()
    except HttpError as exc:
        raise HTTPException(status_code=exc.resp.status, detail=str(exc)) from exc


@router.delete(
    "/watch",
    summary="Stop Gmail push notifications",
    status_code=status.HTTP_200_OK,
)
async def unwatch() -> dict:
    """Revoke the current Gmail push watch for the authenticated user."""
    try:
        stop_watch()
    except HttpError as exc:
        raise HTTPException(
            status_code=exc.resp.status,
            detail=str(exc),
        ) from exc

    return {"status": "ok", "detail": "Gmail watch stopped."}


# ---------------------------------------------------------------------------
# Google OAuth re-authorization (UI-driven)
# ---------------------------------------------------------------------------

# In-memory OAuth flow status — tracks the result of the most recent flow.
_oauth_status: dict[str, Any] = {"status": "idle", "message": None}


@router.post(
    "/auth/start",
    summary="Start Google OAuth authorization flow",
    status_code=status.HTTP_200_OK,
)
async def start_google_auth() -> dict:
    """
    Initiate the Google OAuth2 web flow.

    Returns an authorization URL that the user should open in their browser.
    Poll GET /gmail/auth/status to detect completion.
    """
    settings = get_settings()
    try:
        auth_url, state = create_oauth_flow(settings.gmail_oauth_redirect_uri)
        _oauth_status.update({"status": "pending", "state": state, "message": None})
        return {"status": "pending", "auth_url": auth_url}
    except Exception as exc:
        _oauth_status.update({"status": "error", "message": str(exc), "state": None})
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/auth/callback",
    summary="Google OAuth2 callback",
    include_in_schema=False,
)
async def google_auth_callback(code: str, state: str) -> HTMLResponse:
    """Handles the redirect from Google after the user grants access."""
    try:
        complete_oauth_flow(code, state)
        _oauth_status.update({"status": "success", "message": "Authorization successful.", "state": None})
        html = """<!DOCTYPE html>
<html>
<head><title>Google Authorization</title>
<style>
  body { font-family: sans-serif; display: flex; align-items: center;
         justify-content: center; height: 100vh; margin: 0;
         background: #0f172a; color: #e2e8f0; }
  h2   { color: #4ade80; margin: 0 0 0.5rem; }
  p    { color: #94a3b8; }
</style></head>
<body>
  <div style="text-align:center">
    <div style="font-size:3rem;margin-bottom:1rem">✓</div>
    <h2>Authorization Successful</h2>
    <p>You can close this tab and return to the app.</p>
    <script>setTimeout(() => window.close(), 2000)</script>
  </div>
</body></html>"""
    except Exception as exc:
        _oauth_status.update({"status": "error", "message": str(exc), "state": None})
        html = f"""<!DOCTYPE html>
<html>
<head><title>Google Authorization</title>
<style>
  body {{ font-family: sans-serif; display: flex; align-items: center;
          justify-content: center; height: 100vh; margin: 0;
          background: #0f172a; color: #e2e8f0; }}
  h2   {{ color: #f87171; margin: 0 0 0.5rem; }}
  p    {{ color: #94a3b8; }}
</style></head>
<body>
  <div style="text-align:center">
    <div style="font-size:3rem;margin-bottom:1rem">✗</div>
    <h2>Authorization Failed</h2>
    <p>{str(exc)}</p>
  </div>
</body></html>"""
    return HTMLResponse(content=html)


@router.get(
    "/auth/status",
    summary="Get current Google auth / token status",
    status_code=status.HTTP_200_OK,
)
async def get_google_auth_status() -> dict:
    """
    Returns the combined OAuth flow state and token file health.

    flow_status: idle | pending | success | error
    token_status: valid | expired | missing | invalid | error
    """
    token_info = get_token_status()
    return {
        "flow_status": _oauth_status.get("status", "idle"),
        "flow_message": _oauth_status.get("message"),
        **token_info,
    }


@router.delete(
    "/auth/status",
    summary="Reset Google auth flow status",
    status_code=status.HTTP_200_OK,
)
async def reset_google_auth_status() -> dict:
    """Clear the in-memory OAuth flow status back to idle."""
    _oauth_status.update({"status": "idle", "message": None, "state": None})
    return {"ok": True}
