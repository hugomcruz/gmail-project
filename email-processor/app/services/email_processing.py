"""Shared inbound email processing helpers."""

import logging
from typing import Any

from app.db import crud
from app.db.database import get_session_factory
from app.state import engine

logger = logging.getLogger(__name__)


def _infer_single_inbound_connection_id(conn_type: str) -> str | None:
    """Return an inbound connection id when exactly one exists for the type."""
    matches: list[str] = []
    for conn_id in engine.registry.all_ids():
        try:
            conn = engine.registry.get(conn_id)
        except KeyError:
            continue
        if conn.get("direction") == "inbound" and conn.get("type") == conn_type:
            matches.append(conn_id)
    if len(matches) == 1:
        return matches[0]
    return None


def _normalize_source_fields(email: dict[str, Any]) -> None:
    """Populate normalized source_provider/source_connection fields for rules."""
    source_provider = str(email.get("source_provider") or email.get("provider") or "").strip().lower()
    source_connection = str(email.get("source_connection") or email.get("connection_id") or "").strip()

    if source_connection and not source_provider:
        try:
            source_provider = str(engine.registry.get(source_connection).get("type") or "").strip().lower()
        except KeyError:
            pass

    if source_provider == "gmail" and not source_connection:
        source_connection = _infer_single_inbound_connection_id("gmail") or "gmail"
    elif source_provider == "outlook" and not source_connection:
        inferred = _infer_single_inbound_connection_id("outlook")
        if inferred:
            source_connection = inferred

    if source_provider:
        email["source_provider"] = source_provider
    if source_connection:
        email["source_connection"] = source_connection
        email["connection_id"] = source_connection


def process_inbound_email(email: dict[str, Any]) -> dict[str, Any]:
    """
    Run one inbound email through the rules engine and persist action logs.

    The payload shape matches /internal/process-email input and is reused by
    both internal HTTP ingestion and background inbound pollers.
    """
    _normalize_source_fields(email)

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
                logger.info("Rule '%s' -> %s", r["rule"], actions_summary)
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
        logger.info(
            "No rules matched for email id=%s | subject='%s' | from='%s'",
            email_id,
            subject,
            email.get("from", ""),
        )

    return {"processed": True, "email_id": email_id, "rules_matched": len(results)}
