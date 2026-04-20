"""Pub/Sub push verification and message processing."""

import hashlib
import hmac
import json
import logging
from typing import Any

from notif_receiver.config import get_settings
from notif_receiver.models import GmailNotification, PubSubEnvelope

logger = logging.getLogger(__name__)


def configure_push_subscription() -> None:
    """
    Register the push endpoint with the GCP Pub/Sub subscription.

    Uses Application Default Credentials (ADC) — set GOOGLE_APPLICATION_CREDENTIALS
    to a service-account key file, or let the GCP metadata server provide them
    automatically on Cloud Run / GKE / serverless environments.

    Does nothing if PUBLIC_URL is not configured.
    """
    settings = get_settings()
    if not settings.public_url:
        logger.info("PUBLIC_URL not set — skipping automatic push-subscription registration.")
        return

    from google.cloud import pubsub_v1

    public_url = settings.public_url.rstrip("/")
    push_endpoint = f"{public_url}/pubsub/push?token={settings.pubsub_verification_token}"
    sub_path = (
        f"projects/{settings.google_cloud_project_id}"
        f"/subscriptions/{settings.pubsub_subscription_name}"
    )

    logger.info("Registering Pub/Sub push endpoint: %s", push_endpoint)

    credentials = None
    if settings.gcp_service_account_json:
        import json as _json
        from google.oauth2 import service_account
        sa_info = _json.loads(settings.gcp_service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        logger.info("Using GCP_SERVICE_ACCOUNT_JSON credentials.")

    subscriber = pubsub_v1.SubscriberClient(credentials=credentials)  # None = ADC
    with subscriber:
        subscriber.modify_push_config(
            request={
                "subscription": sub_path,
                "push_config": {"push_endpoint": push_endpoint},
            }
        )
    logger.info("Pub/Sub push endpoint registered successfully.")


def _load_last_history_id() -> str | None:
    """Load the last processed historyId from the database."""
    from notif_receiver.services.token_store import load_history_id
    return load_history_id()


def _save_last_history_id(history_id: str) -> None:
    """Persist the last processed historyId to the database (only advances forward)."""
    from notif_receiver.services.token_store import save_history_id
    save_history_id(history_id)


def initialise_history_id(history_id: str) -> None:
    """
    Seed the database with *history_id* only when no state exists yet.

    Called at startup with the historyId returned by Gmail's watch API so
    that the first incoming notification has a proper baseline to start from,
    instead of falling back to the arbitrary ``notification.historyId - 10``
    heuristic that can inadvertently replay already-processed emails.
    """
    if _load_last_history_id() is None:
        _save_last_history_id(history_id)
        logger.info("Initialised history baseline from watch response: historyId=%s", history_id)


def verify_token(provided_token: str) -> bool:
    """
    Compare the token supplied in the request query-string against the
    configured secret using a constant-time comparison to prevent timing
    attacks.
    """
    settings = get_settings()
    expected = settings.pubsub_verification_token.encode()
    provided = provided_token.encode()
    return hmac.compare_digest(
        hashlib.sha256(expected).digest(),
        hashlib.sha256(provided).digest(),
    )


def parse_gmail_notification(envelope: PubSubEnvelope) -> GmailNotification:
    """
    Decode the Pub/Sub message payload and return a typed GmailNotification.

    Raises:
        ValueError: If the payload cannot be decoded or is missing required fields.
    """
    try:
        data = envelope.message.decode_data()
    except Exception as exc:
        raise ValueError(f"Could not decode Pub/Sub message data: {exc}") from exc

    if "emailAddress" not in data or "historyId" not in data:
        raise ValueError(f"Unexpected Gmail notification payload: {data}")

    return GmailNotification(
        emailAddress=data["emailAddress"],
        historyId=str(data["historyId"]),
    )


def process_notification(notification: GmailNotification) -> dict[str, Any]:
    """
    Entry point for acting on a Gmail change notification.

    Fetches history since the reported historyId, retrieves the full email
    (headers + body + attachments), and forwards it to the email-processor
    service via HTTP POST.
    """
    from notif_receiver.services.gmail_service import list_history, get_full_email  # local import to avoid circular deps

    logger.info(
        "Processing Gmail notification: email=%s historyId=%s",
        notification.emailAddress,
        notification.historyId,
    )

    start_id = _load_last_history_id()
    if start_id is None:
        start_id = str(max(1, int(notification.historyId) - 10))
        logger.info("No previous historyId found, using start_id=%s", start_id)

    # If this notification's historyId is already behind our saved pointer, it's a
    # Pub/Sub retry of something we've already processed — skip re-processing but
    # still return success so Pub/Sub stops retrying.
    if int(notification.historyId) <= int(start_id):
        logger.info(
            "Notification historyId=%s is <= saved pointer %s — already processed, skipping.",
            notification.historyId, start_id,
        )
        return {
            "emailAddress": notification.emailAddress,
            "historyId": notification.historyId,
            "newMessages": [],
        }

    message_ids = list_history(start_id)

    processed_messages: list[dict[str, Any]] = []

    for msg_id in message_ids:
        try:
            email = get_full_email(msg_id)
            if email is None:
                # Skipped — DRAFT, SENT, or unwatched label
                continue
            email.setdefault("provider", "gmail")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch message %s from Gmail: %s", msg_id, exc)
            continue

        try:
            _forward_email(email)
        except Exception as exc:
            logger.error(
                "Failed to forward email id=%s (subject='%s') to email-processor: %s",
                email.get("id"), email.get("subject"), exc,
            )
            continue

        processed_messages.append(email)

    # Save the new pointer AFTER successfully processing all messages.
    # The helper will refuse to advance backwards even if something goes wrong.
    _save_last_history_id(notification.historyId)

    logger.info("Processed %d new message(s).", len(processed_messages))
    return {
        "emailAddress": notification.emailAddress,
        "historyId": notification.historyId,
        "newMessages": [{k: v for k, v in m.items() if k != "body_html"} for m in processed_messages],
    }


def _forward_email(email: dict) -> None:
    """POST the fully-processed email to the email-processor service via HTTP.

    Raises:
        requests.HTTPError: if the email-processor returns a non-2xx response.
        requests.ConnectionError / requests.Timeout: if the service is unreachable.
    """
    import requests
    from notif_receiver.config import get_settings

    settings = get_settings()
    url = f"{settings.email_processor_url.rstrip('/')}/internal/process-email"
    logger.info("Forwarding email id=%s to email-processor at %s", email.get("id"), url)
    resp = requests.post(url, json=email, timeout=30)
    resp.raise_for_status()
    logger.info(
        "Forwarded email id=%s to email-processor (HTTP %s)",
        email.get("id"), resp.status_code,
    )
