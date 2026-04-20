"""SQLAlchemy ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, JSON, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    match: Mapped[str] = mapped_column(String(10), default="all", nullable=False)  # "all" | "any"
    conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    actions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    folder: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def to_engine_dict(self) -> dict:
        """Return a dict in the format expected by RulesEngine."""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "match": self.match,
            "conditions": self.conditions or [],
            "actions": self.actions or [],
        }


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="viewer", nullable=False)  # "admin" | "viewer"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    direction: Mapped[str] = mapped_column(String(20), default="outbound", nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    fields: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def to_registry_dict(self) -> dict:
        """Return a flat dict for the ConnectionRegistry (id + type + all fields merged)."""
        return {"id": self.id, "direction": self.direction, "type": self.type, **(self.fields or {})}


class ActionLog(Base):
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Email details
    email_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    email_subject: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    email_from: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    email_date: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Rule + action details
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)       # "ok" | "error" | "skipped"
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # full action result dict
    triggered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, index=True)
