"""
Email Processor — entry point.

Receives fully-fetched emails from the notif-receiver service via HTTP POST
and runs them through the rules engine.

HTTP API (port 8001):
  POST /internal/process-email  Process a single email through the rules engine
  GET  /health                   Health check
  GET  /rules                    List currently active rules
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.config import get_settings
from app.state import engine
from app.routers.rules_api import router as api_router
from app.routers.onedrive_auth import router as onedrive_auth_router
from app.routers.users import router as users_router
from app.db.database import init_db, get_session_factory
from app.db import crud

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()
logging.getLogger().setLevel(settings.log_level.upper())

# ---------------------------------------------------------------------------
# FastAPI lifespan — DB init + engine setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Init DB and create tables
    init_db()
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        # Create default admin user if no users exist
        from app.auth import hash_password
        created = crud.seed_admin_user(db, hash_password("admin"))
        if created:
            logger.info("Created default admin user — username: admin  password: admin  (change immediately!)")

    # Switch engine to DB mode
    engine.enable_db_mode()

    yield  # app is running

    logger.info("Email processor stopped.")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="HEAT Email Processor", lifespan=lifespan)

# CRUD + metadata API
app.include_router(api_router)
app.include_router(onedrive_auth_router)
app.include_router(users_router)




if settings.health_check_enabled:
    @app.get("/health")
    async def health():
        return {"status": "ok", "rules_loaded": len(engine.rules), "db_mode": engine._db_mode}


@app.get("/internal/engine-status", include_in_schema=False)
async def engine_status():
    """Inspect the currently loaded rules and connections (for debugging)."""
    return {
        "rules_loaded": len(engine.rules),
        "db_mode": engine._db_mode,
        "rules": [
            {
                "name": r.get("name"),
                "enabled": r.get("enabled"),
                "match": r.get("match"),
                "conditions": r.get("conditions", []),
                "actions": [
                    {"type": a.get("type"), "connection": a.get("connection")}
                    for a in r.get("actions", [])
                ],
            }
            for r in engine.rules
        ],
        "connections": engine.registry.all_ids(),
    }


@app.post("/internal/process-email", include_in_schema=False)
async def process_email_internal(email: dict[str, Any]):
    """
    Called by the notif-receiver service with a fully-fetched email dict.
    Runs the email through the rules engine synchronously and returns a summary.
    """
    email_id = email.get("id", "?")
    subject = email.get("subject", "(no subject)")
    logger.info("Received email id=%s | subject='%s'", email_id, subject)

    results = engine.process(email)
    if results:
        SessionLocal = get_session_factory()
        with SessionLocal() as db:
            for r in results:
                actions_summary = ", ".join(
                    f"{a['action']}={a['status']}" for a in r["actions"]
                )
                logger.info("Rule '%s' → %s", r["rule"], actions_summary)
                for a in r["actions"]:
                    crud.create_action_log(
                        db=db,
                        email_id=email_id,
                        email_subject=subject,
                        email_from=email.get("from", ""),
                        email_date=email.get("date"),
                        rule_name=r["rule"],
                        action_type=a.get("action", ""),
                        connection_id=a.get("connection"),
                        status=a.get("status", "unknown"),
                        detail={k: v for k, v in a.items() if k not in ("action", "status", "connection")},
                    )
    else:
        logger.info("No rules matched for email id=%s | subject='%s' | from='%s'",
                    email_id, subject, email.get("from", ""))

    return {"processed": True, "email_id": email_id, "rules_matched": len(results)}


@app.get("/rules")
async def list_active_rules():
    """List the rules currently loaded in the engine."""
    return [
        {
            "name": rule.get("name", "<unnamed>"),
            "match": rule.get("match", "all"),
            "conditions": len(rule.get("conditions", [])),
            "actions": [a.get("type") for a in rule.get("actions", [])],
        }
        for rule in engine.rules
    ]
