"""SQLAlchemy database engine and session factory."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

from app.config import get_settings

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10},
        )
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _SessionLocal


def init_db() -> None:
    """Create all tables if they don't exist yet."""
    from app.db.models import Base  # noqa: F401 — import triggers table registration
    engine = get_engine()
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    conn_cols = {col["name"] for col in inspector.get_columns("connections")}
    if "direction" not in conn_cols:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE connections ADD COLUMN direction VARCHAR(20) NOT NULL DEFAULT 'outbound'")
            )
            conn.execute(
                text(
                    """
                    UPDATE connections
                    SET direction = CASE
                        WHEN type IN ('gmail', 'outlook', 'outlook365') THEN 'inbound'
                        ELSE 'outbound'
                    END
                    """
                )
            )

    rule_cols = {col["name"] for col in inspector.get_columns("rules")}
    if "folder" not in rule_cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE rules ADD COLUMN folder VARCHAR(255) NOT NULL DEFAULT ''"))


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a DB session and closes it after the request."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
