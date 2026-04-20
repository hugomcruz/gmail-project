"""CRUD operations for Rules and Users."""

from sqlalchemy.orm import Session

from app.db.models import ActionLog, Connection, Rule, User
from app.db.schemas import RuleCreate, RuleUpdate, UserCreate, UserUpdate


# ── Rules ────────────────────────────────────────────────────────────────────

def get_rules(db: Session, skip: int = 0, limit: int = 500) -> list[Rule]:
    return db.query(Rule).order_by(Rule.id).offset(skip).limit(limit).all()


def get_rule(db: Session, rule_id: int) -> Rule | None:
    return db.query(Rule).filter(Rule.id == rule_id).first()


def create_rule(db: Session, rule: RuleCreate) -> Rule:
    db_rule = Rule(
        name=rule.name,
        enabled=rule.enabled,
        match=rule.match,
        folder=rule.folder,
        conditions=[c.model_dump() for c in rule.conditions],
        actions=[a.model_dump() for a in rule.actions],
    )
    db.add(db_rule)
    db.commit()
    db.refresh(db_rule)
    return db_rule


def update_rule(db: Session, rule_id: int, rule: RuleUpdate) -> Rule | None:
    db_rule = get_rule(db, rule_id)
    if not db_rule:
        return None

    update_data = rule.model_dump(exclude_unset=True)
    # Serialize nested Pydantic models if present
    if "conditions" in update_data and update_data["conditions"] is not None:
        update_data["conditions"] = [
            c.model_dump() if hasattr(c, "model_dump") else c
            for c in update_data["conditions"]
        ]
    if "actions" in update_data and update_data["actions"] is not None:
        update_data["actions"] = [
            a.model_dump() if hasattr(a, "model_dump") else a
            for a in update_data["actions"]
        ]

    for key, value in update_data.items():
        setattr(db_rule, key, value)

    db.commit()
    db.refresh(db_rule)
    return db_rule


def delete_rule(db: Session, rule_id: int) -> bool:
    db_rule = get_rule(db, rule_id)
    if not db_rule:
        return False
    db.delete(db_rule)
    db.commit()
    return True


def seed_from_yaml(db: Session, rules_list: list[dict]) -> int:
    """Import rules from a YAML-parsed list if the DB is empty. Returns count inserted."""
    if db.query(Rule).count() > 0:
        return 0

    count = 0
    for r in rules_list:
        db_rule = Rule(
            name=r.get("name", "Unnamed"),
            enabled=r.get("enabled", True),
            match=r.get("match", "all"),
            conditions=r.get("conditions", []),
            actions=r.get("actions", []),
        )
        db.add(db_rule)
        count += 1

    db.commit()
    return count


# ── Users ────────────────────────────────────────────────────────────────────

def get_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.id).all()


def get_user(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def create_user(db: Session, payload: UserCreate, hashed_password: str) -> User:
    db_user = User(
        username=payload.username,
        hashed_password=hashed_password,
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def update_user(db: Session, user_id: int, payload: UserUpdate, hashed_password: str | None) -> User | None:
    db_user = get_user(db, user_id)
    if not db_user:
        return None
    update_data = payload.model_dump(exclude_unset=True)
    update_data.pop("password", None)  # never store plain password
    if hashed_password is not None:
        update_data["hashed_password"] = hashed_password
    for key, value in update_data.items():
        setattr(db_user, key, value)
    db.commit()
    db.refresh(db_user)
    return db_user


def delete_user(db: Session, user_id: int) -> bool:
    db_user = get_user(db, user_id)
    if not db_user:
        return False
    db.delete(db_user)
    db.commit()
    return True


def seed_admin_user(db: Session, hashed_password: str) -> bool:
    """Create a default admin user if no users exist. Returns True if created."""
    if db.query(User).count() > 0:
        return False
    db_user = User(
        username="admin",
        hashed_password=hashed_password,
        role="admin",
        is_active=True,
    )
    db.add(db_user)
    db.commit()
    return True


# ── Connections ───────────────────────────────────────────────────────────────

def get_connections(db: Session) -> list[Connection]:
    return db.query(Connection).order_by(Connection.id).all()


def get_connection(db: Session, conn_id: str) -> Connection | None:
    return db.query(Connection).filter(Connection.id == conn_id).first()


def create_connection(db: Session, conn_id: str, direction: str, conn_type: str, fields: dict) -> Connection:
    conn = Connection(id=conn_id, direction=direction, type=conn_type, fields=fields)
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def update_connection(db: Session, conn_id: str, direction: str, conn_type: str, fields: dict) -> Connection | None:
    conn = get_connection(db, conn_id)
    if not conn:
        return None
    conn.direction = direction
    conn.type = conn_type
    conn.fields = fields
    db.commit()
    db.refresh(conn)
    return conn


def delete_connection(db: Session, conn_id: str) -> bool:
    conn = get_connection(db, conn_id)
    if not conn:
        return False
    db.delete(conn)
    db.commit()
    return True


def seed_connections_from_yaml(db: Session, yaml_connections: list[dict]) -> int:
    """Seed connections from a YAML-parsed list if the DB has no connections. Returns count inserted."""
    if db.query(Connection).count() > 0:
        return 0
    count = 0
    for c in yaml_connections:
        conn_id = c.get("id", "").strip()
        direction = c.get("direction", "").strip() or ("inbound" if c.get("type") in {"gmail", "outlook", "outlook365"} else "outbound")
        conn_type = c.get("type", "").strip()
        if not conn_id or not conn_type:
            continue
        fields = {k: v for k, v in c.items() if k not in ("id", "direction", "type")}
        db.add(Connection(id=conn_id, direction=direction, type=conn_type, fields=fields))
        count += 1
    db.commit()
    return count


# ── Action Logs ───────────────────────────────────────────────────────────────

def create_action_log(
    db: Session,
    email_id: str,
    email_subject: str,
    email_from: str,
    email_date: str | None,
    rule_name: str,
    action_type: str,
    connection_id: str | None,
    status: str,
    detail: dict | None,
) -> ActionLog:
    log = ActionLog(
        email_id=email_id,
        email_subject=email_subject,
        email_from=email_from,
        email_date=email_date,
        rule_name=rule_name,
        action_type=action_type,
        connection_id=connection_id,
        status=status,
        detail=detail,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_action_logs(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    rule_name: str | None = None,
    status: str | None = None,
) -> list[ActionLog]:
    q = db.query(ActionLog).order_by(ActionLog.triggered_at.desc())
    if rule_name:
        q = q.filter(ActionLog.rule_name == rule_name)
    if status:
        q = q.filter(ActionLog.status == status)
    return q.offset(skip).limit(limit).all()


def count_action_logs(
    db: Session,
    rule_name: str | None = None,
    status: str | None = None,
) -> int:
    q = db.query(ActionLog)
    if rule_name:
        q = q.filter(ActionLog.rule_name == rule_name)
    if status:
        q = q.filter(ActionLog.status == status)
    return q.count()
