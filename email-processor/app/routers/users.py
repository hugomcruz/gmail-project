"""Auth (login) + User management API router."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.db import crud
from app.db.database import get_db
from app.db.models import User
from app.db.schemas import (
    LoginRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.post("/auth/login", response_model=TokenResponse, tags=["Auth"])
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate with username + password; returns a JWT access token."""
    user = crud.get_user_by_username(db, payload.username)
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    token = create_access_token(user.id, user.username, user.role)
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.get("/auth/me", response_model=UserResponse, tags=["Auth"])
def me(current_user: Annotated[User, Depends(get_current_user)]):
    """Return the currently authenticated user."""
    return current_user


# ---------------------------------------------------------------------------
# User management (admin only)
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[UserResponse], tags=["Users"])
def list_users(
    _admin: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    return crud.get_users(db)


@router.post("/users", response_model=UserResponse, status_code=201, tags=["Users"])
def create_user(
    payload: UserCreate,
    _admin: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    if crud.get_user_by_username(db, payload.username):
        raise HTTPException(status_code=409, detail=f"Username '{payload.username}' already exists")
    hashed = hash_password(payload.password)
    return crud.create_user(db, payload, hashed)


@router.put("/users/{user_id}", response_model=UserResponse, tags=["Users"])
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_admin: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    # Prevent renaming to an existing username
    if payload.username:
        existing = crud.get_user_by_username(db, payload.username)
        if existing and existing.id != user_id:
            raise HTTPException(status_code=409, detail=f"Username '{payload.username}' already exists")

    hashed = hash_password(payload.password) if payload.password else None
    user = crud.update_user(db, user_id, payload, hashed)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.delete("/users/{user_id}", status_code=204, tags=["Users"])
def delete_user(
    user_id: int,
    current_admin: Annotated[User, Depends(require_admin)],
    db: Session = Depends(get_db),
):
    # Prevent self-deletion
    if current_admin.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if not crud.delete_user(db, user_id):
        raise HTTPException(status_code=404, detail="User not found")
