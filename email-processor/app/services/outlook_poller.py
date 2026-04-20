"""Background poller for authenticated inbound Outlook connections."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.services.outlook_inbound_service import sync_all_outlook_connections

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _run() -> None:
    interval = max(15, int(get_settings().outlook_poll_interval_seconds))
    logger.info("Outlook inbound poller started (interval=%ss)", interval)
    while True:
        try:
            await asyncio.to_thread(sync_all_outlook_connections)
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Outlook inbound poll cycle failed: %s", exc)
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
    logger.info("Outlook inbound poller stopped")
