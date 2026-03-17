"""Pub/Sub push-subscription webhook endpoint."""

import logging

from fastapi import APIRouter, HTTPException, Query, status

from notif_receiver.models import PubSubEnvelope
from notif_receiver.services.pubsub_service import parse_gmail_notification, verify_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pubsub", tags=["Pub/Sub"])


@router.post(
    "/push",
    summary="Receive Pub/Sub push notifications from Gmail",
    status_code=status.HTTP_200_OK,
)
async def pubsub_push(
    envelope: PubSubEnvelope,
    token: str = Query(..., description="Verification token set in the Pub/Sub push URL"),
) -> dict:
    """
    Endpoint registered as the Pub/Sub push subscription URL.

    Verifies the token, decodes the notification, then enqueues it into the
    in-process notification queue and returns HTTP 200 immediately.
    The notification_worker thread pool handles the actual Gmail API fetch
    and forwards the full email to the email-processor service.

    Returning 200 quickly prevents Pub/Sub from retrying due to latency.
    """
    if not verify_token(token):
        logger.warning("Received Pub/Sub push with invalid token.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid verification token.",
        )

    try:
        notification = parse_gmail_notification(envelope)
    except ValueError as exc:
        # Return 200 so Pub/Sub does not endlessly retry a malformed message.
        logger.error("Could not parse Gmail notification: %s", exc)
        return {"status": "ignored", "reason": str(exc)}

    from notif_receiver.services.notification_worker import get_queue
    await get_queue().put(notification)

    return {"status": "queued", "historyId": notification.historyId}
