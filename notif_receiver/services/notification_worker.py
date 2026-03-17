"""
Notification worker — processes Gmail notifications via an in-process queue.

When a Pub/Sub push (or pull) arrives the HTTP handler enqueues the raw
notification so it can return HTTP 200 quickly without blocking on Gmail API
calls.  This worker drains the queue asynchronously, dispatching the slow
Gmail API work to a thread pool.  On completion, the full email is forwarded
to the email-processor service via HTTP POST.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from notif_receiver.models import GmailNotification

logger = logging.getLogger(__name__)

_queue: asyncio.Queue | None = None
_executor: ThreadPoolExecutor | None = None
_worker_task: asyncio.Task | None = None


def get_queue() -> asyncio.Queue:
    """Return the shared notification queue (created lazily on first call)."""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def start() -> None:
    """Start the thread pool and begin draining the notification queue."""
    global _executor, _worker_task
    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notif-worker")
    _worker_task = asyncio.create_task(_drain())
    logger.info("Notification worker started (in-process queue, thread pool: 4 workers)")


async def _drain() -> None:
    """Async task — continuously drains the notification queue."""
    queue = get_queue()
    while True:
        notification: GmailNotification | None = None
        try:
            notification = await queue.get()
            logger.info(
                "Worker received notification: email=%s historyId=%s",
                notification.emailAddress,
                notification.historyId,
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_executor, _process, notification)
            queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("Worker failed to handle notification: %s", exc)
            if notification is not None:
                queue.task_done()


def _process(notification: GmailNotification) -> None:
    """
    Synchronous processor — runs inside the thread pool.
    Calls Gmail API (blocking I/O) and forwards the full email to the
    email-processor service via HTTP POST.

    Any exception (Gmail API error, connection error to email-processor,
    non-2xx HTTP response) propagates to the caller so it is logged
    prominently rather than swallowed.
    """
    from notif_receiver.services.pubsub_service import process_notification

    process_notification(notification)


async def stop() -> None:
    """Cancel the drain task and shut down the thread pool."""
    global _worker_task, _executor

    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None

    if _executor:
        _executor.shutdown(wait=False)
        _executor = None

    logger.info("Notification worker stopped.")
