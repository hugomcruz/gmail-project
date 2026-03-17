"""
Periodic Gmail watch renewal.

Gmail push notifications expire after ~7 days.  This module starts an
asyncio background task that renews the watch every RENEWAL_INTERVAL_DAYS
days so notifications never silently stop.
"""

import asyncio
import logging

from notif_receiver.config import get_settings
from notif_receiver.services.gmail_service import start_watch

logger = logging.getLogger(__name__)

# Renew daily — well within the 7-day expiry window
RENEWAL_INTERVAL_SECONDS = 24 * 60 * 60  # 1 day

_renewer_task: asyncio.Task | None = None


def _build_topic_name() -> str:
    settings = get_settings()
    return f"projects/{settings.google_cloud_project_id}/topics/{settings.pubsub_topic_name}"


def _renew() -> str | None:
    """Synchronous renewal call (runs in thread pool or directly at startup).

    Returns the historyId from the watch response, or None on failure.
    """
    settings = get_settings()
    topic = _build_topic_name()
    try:
        response = start_watch(
            topic_name=topic,
            label_ids=settings.watched_labels,
        )
        history_id = str(response.get("historyId", ""))
        logger.info(
            "Gmail watch renewed — historyId=%s expiration=%s",
            history_id,
            response.get("expiration"),
        )
        return history_id or None
    except Exception:
        logger.exception("Failed to renew Gmail watch — will retry at next interval")
        return None


async def _renewal_loop() -> None:
    """Async loop: renew immediately, then every RENEWAL_INTERVAL_SECONDS."""
    loop = asyncio.get_running_loop()

    # Renew on startup so the watch is always fresh after a restart.
    # Seed the database with the returned historyId if no state exists
    # yet — this prevents the first notification from falling back to the
    # arbitrary `historyId - 10` heuristic and replaying old emails.
    startup_history_id = await loop.run_in_executor(None, _renew)
    if startup_history_id:
        from notif_receiver.services.pubsub_service import initialise_history_id
        initialise_history_id(startup_history_id)

    while True:
        await asyncio.sleep(RENEWAL_INTERVAL_SECONDS)
        await loop.run_in_executor(None, _renew)


async def start() -> None:
    """Start the background renewal task."""
    global _renewer_task
    _renewer_task = asyncio.create_task(_renewal_loop())
    logger.info(
        "Gmail watch renewer started (interval: %d hour(s))",
        RENEWAL_INTERVAL_SECONDS // 3600,
    )


async def stop() -> None:
    """Cancel the background renewal task."""
    global _renewer_task
    if _renewer_task and not _renewer_task.done():
        _renewer_task.cancel()
        try:
            await _renewer_task
        except asyncio.CancelledError:
            pass
    _renewer_task = None
    logger.info("Gmail watch renewer stopped")
