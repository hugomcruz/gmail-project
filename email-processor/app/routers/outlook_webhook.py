"""Microsoft Graph webhook endpoint for Outlook inbound notifications."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.services.outlook_webhook_service import process_outlook_notifications

router = APIRouter(prefix="/api/inbound-webhooks", tags=["Inbound Webhooks"])


@router.get("/outlook")
def validate_outlook_webhook(validation_token: str | None = Query(default=None, alias="validationToken")):
    """
    Microsoft Graph validation handshake.

    Graph calls this endpoint with ?validationToken=... and expects the raw token text.
    """
    if validation_token:
        return PlainTextResponse(content=validation_token)
    return {"status": "ok"}


async def _process_notifications_background(body: dict[str, Any]) -> None:
    """Run notification processing in a thread pool to avoid blocking the event loop."""
    await asyncio.to_thread(process_outlook_notifications, body)


@router.post("/outlook")
async def receive_outlook_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    validation_token: str | None = Query(default=None, alias="validationToken"),
):
    # Some Graph paths may send validation token on POST as well.
    if validation_token:
        return PlainTextResponse(content=validation_token)

    body: dict[str, Any] = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = {}

    background_tasks.add_task(_process_notifications_background, body)
    # Graph requires a 202 response within 3 seconds; 200 also works but 202 is spec-correct.
    return JSONResponse(content={"status": "accepted"}, status_code=202)
