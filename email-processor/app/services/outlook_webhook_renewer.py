"""Background renewer for Outlook Graph webhook subscriptions."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.services.outlook_webhook_service import ensure_all_outlook_subscriptions

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _run() -> None:
    interval = max(120, int(get_settings().outlook_webhook_renew_interval_seconds))
    logger.info("Outlook webhook renewer started (interval=%ss)", interval)

    while True:
        try:
            await asyncio.to_thread(ensure_all_outlook_subscriptions)
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Outlook webhook renew cycle failed: %s", exc)
        await asyncio.sleep(interval)


async def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_run())


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    logger.info("Outlook webhook renewer stopped")
