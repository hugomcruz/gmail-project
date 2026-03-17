"""REST API router — CRUD for Rules + Connections + metadata endpoints."""

import logging
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.db.crud import (
    create_rule, delete_rule, get_rule, get_rules, seed_from_yaml, update_rule,
    get_connections as crud_get_connections,
    get_connection as crud_get_connection,
    create_connection as crud_create_connection,
    update_connection as crud_update_connection,
    delete_connection as crud_delete_connection,
    get_action_logs as crud_get_action_logs,
    count_action_logs as crud_count_action_logs,
)
from app.db.database import get_db
from app.db.models import User
from app.db.schemas import RuleCreate, RuleResponse, RuleUpdate, ActionLogResponse
from app.state import engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])

# ── Condition + action metadata (used by the UI for dropdowns) ──────────────

CONDITION_TYPES = [
    {"value": "from_equals",         "label": "From equals"},
    {"value": "from_contains",       "label": "From contains"},
    {"value": "to_contains",         "label": "To contains"},
    {"value": "subject_equals",      "label": "Subject equals"},
    {"value": "subject_contains",    "label": "Subject contains"},
    {"value": "subject_starts_with", "label": "Subject starts with"},
    {"value": "subject_ends_with",   "label": "Subject ends with"},
    {"value": "body_contains",       "label": "Body contains"},
    {"value": "has_attachments",     "label": "Has attachments"},
    {"value": "attachment_count_gte","label": "Attachment count ≥"},
    {"value": "label_contains",      "label": "Label contains"},
]

ACTION_TYPES = [
    {"value": "upload_to_s3",     "label": "Upload to S3"},
    {"value": "upload_to_onedrive","label": "Upload to OneDrive"},
    {"value": "create_jira_task", "label": "Create JIRA task"},
    {"value": "forward_email",    "label": "Forward email"},
]

# conditions that don't need a value
NO_VALUE_CONDITIONS = {"has_attachments"}


# ── Metadata endpoints ───────────────────────────────────────────────────────

@router.get("/meta/condition-types")
def get_condition_types():
    return CONDITION_TYPES


@router.get("/meta/action-types")
def get_action_types():
    return ACTION_TYPES


@router.get("/meta/connections")
def get_connections():
    """Return all connection IDs and types from the database."""
    try:
        conns = []
        for conn_id in engine.registry.all_ids():
            conn = engine.registry.get(conn_id)
            conns.append({
                "id": conn_id,
                "type": conn.get("type"),
                "label": f"{conn_id} ({conn.get('type')})",
            })
        return conns
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Rules CRUD ───────────────────────────────────────────────────────────────

@router.get("/rules", response_model=list[RuleResponse])
def list_rules(db: Session = Depends(get_db)):
    return get_rules(db)


@router.post("/rules", response_model=RuleResponse, status_code=201)
def create(payload: RuleCreate, db: Session = Depends(get_db)):
    rule = create_rule(db, payload)
    engine.reload()
    return rule


@router.get("/rules/{rule_id}", response_model=RuleResponse)
def get_one(rule_id: int, db: Session = Depends(get_db)):
    rule = get_rule(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.put("/rules/{rule_id}", response_model=RuleResponse)
def update(rule_id: int, payload: RuleUpdate, db: Session = Depends(get_db)):
    rule = update_rule(db, rule_id, payload)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    engine.reload()
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
def delete(rule_id: int, db: Session = Depends(get_db)):
    if not delete_rule(db, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    engine.reload()


@router.post("/rules/{rule_id}/toggle", response_model=RuleResponse)
def toggle(rule_id: int, db: Session = Depends(get_db)):
    rule = get_rule(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.enabled = not rule.enabled
    db.commit()
    db.refresh(rule)
    engine.reload()
    return rule


@router.post("/rules/reload")
def reload_rules():
    """Hot-reload rules from the database into the running engine."""
    try:
        engine.reload()
        logger.info("Rules reloaded via API — %d rule(s) active", len(engine.rules))
        return {
            "status": "ok",
            "rules_loaded": len(engine.rules),
            "message": f"Reloaded {len(engine.rules)} rule(s) from database",
        }
    except Exception as exc:
        logger.error("Rules reload failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/rules/import-yaml", status_code=200)
def import_yaml(db: Session = Depends(get_db), rules_file: str = "rules.yaml"):
    """Import rules from rules.yaml into the database (only if DB is empty)."""
    try:
        with open(rules_file) as f:
            data = yaml.safe_load(f) or {}
        rules_list = data.get("rules", [])
        count = seed_from_yaml(db, rules_list)
        return {"imported": count, "message": f"Imported {count} rule(s) from {rules_file}"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"{rules_file} not found")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Connections CRUD ─────────────────────────────────────────────────────────
# Connections are stored in the database.
# After any mutation the ConnectionRegistry inside the engine is reloaded.

class ConnectionPayload(BaseModel):
    id: str
    type: str
    # All other fields are stored verbatim (bucket, url, token, etc.)
    fields: dict[str, Any] = {}


def _to_payload(conn) -> dict:
    """Convert a Connection DB row to a response dict."""
    return {"id": conn.id, "type": conn.type, "fields": conn.fields or {}}


@router.get("/connections")
def list_connections(db: Session = Depends(get_db)):
    """Return all connections from the database."""
    return [_to_payload(c) for c in crud_get_connections(db)]


@router.post("/connections", status_code=201)
def create_connection(payload: ConnectionPayload, db: Session = Depends(get_db)):
    """Add a new connection to the database."""
    if crud_get_connection(db, payload.id):
        raise HTTPException(status_code=409, detail=f"Connection '{payload.id}' already exists.")
    conn = crud_create_connection(db, payload.id, payload.type, payload.fields)
    engine.reload_connections()
    return _to_payload(conn)


@router.put("/connections/{conn_id}")
def update_connection(conn_id: str, payload: ConnectionPayload, db: Session = Depends(get_db)):
    """Update an existing connection in the database."""
    conn = crud_update_connection(db, conn_id, payload.type, payload.fields)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Connection '{conn_id}' not found.")
    engine.reload_connections()
    return _to_payload(conn)


@router.delete("/connections/{conn_id}", status_code=204)
def delete_connection(conn_id: str, db: Session = Depends(get_db)):
    """Remove a connection from the database."""
    if not crud_delete_connection(db, conn_id):
        raise HTTPException(status_code=404, detail=f"Connection '{conn_id}' not found.")
    engine.reload_connections()


# ── Action Logs ───────────────────────────────────────────────────────────────

@router.get("/logs", response_model=list[ActionLogResponse])
def list_logs(
    skip: int = 0,
    limit: int = 100,
    rule_name: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """Return action log entries, newest first. Supports filtering by rule_name and status."""
    return crud_get_action_logs(db, skip=skip, limit=limit, rule_name=rule_name, status=status)


@router.get("/logs/count")
def count_logs(
    rule_name: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    return {"count": crud_count_action_logs(db, rule_name=rule_name, status=status)}
