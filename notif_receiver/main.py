"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from notif_receiver.config import get_settings
from notif_receiver.routers import gmail, pubsub

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the notification worker (drains in-process queue via thread pool)
    from notif_receiver.services import notification_worker
    from notif_receiver.services import watch_renewer
    await notification_worker.start()
    await watch_renewer.start()

    if settings.use_pull_subscriber:
        from notif_receiver.services.pull_subscriber import start_pull_subscriber, stop_pull_subscriber
        logger.info("USE_PULL_SUBSCRIBER=true — starting pull subscriber (no public URL needed).")
        start_pull_subscriber()
        yield
        stop_pull_subscriber()
    else:
        yield

    await watch_renewer.stop()
    await notification_worker.stop()


app = FastAPI(
    title="Gmail Pub/Sub Processor",
    lifespan=lifespan,
    description=(
        "FastAPI service that receives Gmail change notifications via "
        "Google Cloud Pub/Sub (push or pull) and processes them."
    ),
    version="1.0.0",
)

# Allow all origins in development; tighten in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(pubsub.router)
app.include_router(gmail.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
if get_settings().health_check_enabled:
    @app.get("/health", tags=["Health"])
    async def health() -> dict:
        """Simple liveness probe."""
        return {"status": "ok"}


# ---------------------------------------------------------------------------
# UI redirect — the Rules UI lives on email-processor (port 8001)
# ---------------------------------------------------------------------------
@app.get("/ui", include_in_schema=False)
@app.get("/ui/{path:path}", include_in_schema=False)
async def ui_redirect(path: str = "") -> RedirectResponse:
    target = f"http://localhost:8001/ui/{path}"
    return RedirectResponse(url=target)


# ---------------------------------------------------------------------------
# Dev server
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "notif_receiver.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
