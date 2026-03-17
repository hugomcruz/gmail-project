"""
Pub/Sub *pull* subscriber worker.

Runs as a background thread and continuously pulls messages from the
subscription, so no public HTTPS endpoint is needed (works on localhost /
behind a firewall).

Start it by setting USE_PULL_SUBSCRIBER=true in .env.

Note: When using the pull API, message.data arrives as raw bytes (already
decoded). The push API encodes it as base64. We handle both paths separately.
"""

import json
import logging
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError

from google.cloud import pubsub_v1
from google.api_core.exceptions import GoogleAPICallError

from notif_receiver.config import get_settings
from notif_receiver.services.pubsub_service import process_notification
from notif_receiver.models import GmailNotification

logger = logging.getLogger(__name__)

_subscriber_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _handle_message(message: pubsub_v1.subscriber.message.Message) -> None:
    """Callback invoked for each pulled Pub/Sub message."""
    try:
        # Pull API delivers data as raw bytes (not base64-encoded, unlike push).
        raw = message.data if isinstance(message.data, bytes) else message.data.encode()
        data = json.loads(raw.decode("utf-8"))

        if "emailAddress" not in data or "historyId" not in data:
            logger.warning("Unexpected Pub/Sub payload, acking to avoid retry: %s", data)
            message.ack()
            return

        notification = GmailNotification(
            emailAddress=data["emailAddress"],
            historyId=str(data["historyId"]),
        )
        result = process_notification(notification)
        logger.info("Pull-processed notification: %s", result)
        message.ack()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error handling pulled message, nacking: %s", exc)
        message.nack()


def _run_subscriber() -> None:
    settings = get_settings()
    subscription_path = (
        f"projects/{settings.google_cloud_project_id}"
        f"/subscriptions/{settings.pubsub_subscription_name}"
    )
    logger.info("Pull subscriber starting on %s", subscription_path)

    import os
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    # Use a dedicated token with cloud-platform scope for Pub/Sub access.
    # This is separate from the Gmail token which only has gmail.* scopes.
    PUBSUB_TOKEN_FILE = "token_setup.json"
    PUBSUB_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

    creds = None
    if os.path.exists(PUBSUB_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(PUBSUB_TOKEN_FILE, PUBSUB_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            import json as _json
            from notif_receiver.services.token_store import load_client_secret
            client_secret_json = load_client_secret()
            if not client_secret_json:
                raise RuntimeError(
                    "No OAuth2 client secret found. Set GMAIL_CLIENT_SECRET_JSON "
                    "or seed the database with the client_secret.json contents."
                )
            flow = InstalledAppFlow.from_client_config(
                _json.loads(client_secret_json), PUBSUB_SCOPES
            )
            creds = flow.run_local_server(port=8888)
        with open(PUBSUB_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    subscriber = pubsub_v1.SubscriberClient(credentials=creds)

    with subscriber:
        streaming_pull = subscriber.subscribe(subscription_path, callback=_handle_message)
        logger.info("Pull subscriber listening …")
        try:
            while not _stop_event.is_set():
                try:
                    streaming_pull.result(timeout=5)
                except FuturesTimeoutError:
                    pass  # keep looping until stop_event is set
        except (GoogleAPICallError, Exception) as exc:
            logger.error("Pull subscriber error: %s", exc)
            streaming_pull.cancel()
            streaming_pull.result()


def start_pull_subscriber() -> None:
    """Start the pull subscriber in a daemon background thread."""
    global _subscriber_thread
    _stop_event.clear()
    _subscriber_thread = threading.Thread(target=_run_subscriber, daemon=True, name="pubsub-pull")
    _subscriber_thread.start()
    logger.info("Pull subscriber thread started.")


def stop_pull_subscriber() -> None:
    """Signal the pull subscriber thread to stop."""
    _stop_event.set()
    if _subscriber_thread:
        _subscriber_thread.join(timeout=10)
    logger.info("Pull subscriber thread stopped.")
