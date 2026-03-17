"""Pydantic schemas for the Rules API and Auth."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class ConditionSchema(BaseModel):
    type: str
    value: str | int | None = None
    case_sensitive: bool = False


class ActionSchema(BaseModel):
    type: str
    connection: str
    config: dict[str, Any] = Field(default_factory=dict)


class RuleCreate(BaseModel):
    name: str = Field(..., min_length=1)
    enabled: bool = True
    match: str = Field("all", pattern="^(all|any)$")
    conditions: list[ConditionSchema] = Field(default_factory=list)
    actions: list[ActionSchema] = Field(default_factory=list)


class RuleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    enabled: bool | None = None
    match: str | None = Field(None, pattern="^(all|any)$")
    conditions: list[ConditionSchema] | None = None
    actions: list[ActionSchema] | None = None


class RuleResponse(BaseModel):
    id: int
    name: str
    enabled: bool
    match: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── User schemas ─────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=6)
    role: str = Field("viewer", pattern="^(admin|viewer)$")
    is_active: bool = True


class UserUpdate(BaseModel):
    username: str | None = Field(None, min_length=1, max_length=150)
    password: str | None = Field(None, min_length=6)
    role: str | None = Field(None, pattern="^(admin|viewer)$")
    is_active: bool | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class ActionLogResponse(BaseModel):
    id: int
    email_id: str
    email_subject: str
    email_from: str
    email_date: str | None
    rule_name: str
    action_type: str
    connection_id: str | None
    status: str
    detail: dict[str, Any] | None
    triggered_at: datetime

    model_config = {"from_attributes": True}
