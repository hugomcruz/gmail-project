"""
Database-backed storage for OAuth tokens, client credentials, and Gmail
history state.

All data is kept in the ``gmail_oauth_tokens`` key-value table so nothing
depends on volume-mounted files, making the service compatible with
stateless / serverless deployments.
"""

import logging
import os
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from notif_receiver.config import get_settings

logger = logging.getLogger(__name__)

_TOKEN_KEY = "gmail_oauth"
_CLIENT_SECRET_KEY = "gmail_client_secret"
_HISTORY_STATE_KEY = "gmail_history_state"
_engine: Optional[Engine] = None


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10},
        )
        _ensure_table(_engine)
    return _engine


def _ensure_table(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS gmail_oauth_tokens (
                key         TEXT PRIMARY KEY,
                token_json  TEXT NOT NULL,
                updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        conn.commit()


def load_token() -> Optional[str]:
    """Return the stored OAuth token JSON, or None if not found."""
    try:
        with _get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT token_json FROM gmail_oauth_tokens WHERE key = :key"),
                {"key": _TOKEN_KEY},
            ).fetchone()
            return row[0] if row else None
    except Exception as exc:
        logger.warning("Could not load token from database: %s", exc)
        return None


def save_token(token_json: str) -> None:
    """Upsert the OAuth token JSON into the database."""
    try:
        with _get_engine().connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO gmail_oauth_tokens (key, token_json, updated_at)
                    VALUES (:key, :token_json, NOW())
                    ON CONFLICT (key) DO UPDATE
                        SET token_json = EXCLUDED.token_json,
                            updated_at = NOW()
                """),
                {"key": _TOKEN_KEY, "token_json": token_json},
            )
            conn.commit()
        logger.debug("OAuth token saved to database.")
    except Exception as exc:
        logger.warning("Could not save token to database: %s", exc)


# ---------------------------------------------------------------------------
# Client secret (replaces client_secret.json volume mount)
# ---------------------------------------------------------------------------

def _upsert(engine: Engine, key: str, value: str) -> None:
    """Generic upsert into the gmail_oauth_tokens key-value table."""
    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO gmail_oauth_tokens (key, token_json, updated_at)
                VALUES (:key, :token_json, NOW())
                ON CONFLICT (key) DO UPDATE
                    SET token_json = EXCLUDED.token_json,
                        updated_at = NOW()
            """),
            {"key": key, "token_json": value},
        )
        conn.commit()


def load_client_secret() -> Optional[str]:
    """
    Return the OAuth2 client secret JSON string.

    Resolution order:
    1. Database (primary)
    2. ``GMAIL_CLIENT_SECRET_JSON`` env var — seeded once into DB on first read
    3. File at ``settings.gmail_credentials_file`` — migration path for local dev
    """
    try:
        with _get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT token_json FROM gmail_oauth_tokens WHERE key = :key"),
                {"key": _CLIENT_SECRET_KEY},
            ).fetchone()
            if row:
                return row[0]
    except Exception as exc:
        logger.warning("Could not load client secret from database: %s", exc)

    settings = get_settings()

    # Env var seed (serverless deployments)
    if settings.gmail_client_secret_json:
        try:
            _upsert(_get_engine(), _CLIENT_SECRET_KEY, settings.gmail_client_secret_json)
            logger.info("Client secret seeded from GMAIL_CLIENT_SECRET_JSON env var into database.")
        except Exception as exc:
            logger.warning("Could not persist client secret to database: %s", exc)
        return settings.gmail_client_secret_json

    # File migration (local dev)
    if os.path.exists(settings.gmail_credentials_file):
        with open(settings.gmail_credentials_file) as f:
            content = f.read()
        try:
            _upsert(_get_engine(), _CLIENT_SECRET_KEY, content)
            logger.info("Client secret migrated from %s to database.", settings.gmail_credentials_file)
        except Exception as exc:
            logger.warning("Could not persist migrated client secret to database: %s", exc)
        return content

    return None


def save_client_secret(json_str: str) -> None:
    """Persist the OAuth2 client secret JSON into the database."""
    try:
        _upsert(_get_engine(), _CLIENT_SECRET_KEY, json_str)
        logger.debug("Client secret saved to database.")
    except Exception as exc:
        logger.warning("Could not save client secret to database: %s", exc)


# ---------------------------------------------------------------------------
# Gmail history state (replaces history_state.json volume mount)
# ---------------------------------------------------------------------------

def load_history_id() -> Optional[str]:
    """Return the last processed Gmail historyId from the database."""
    try:
        with _get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT token_json FROM gmail_oauth_tokens WHERE key = :key"),
                {"key": _HISTORY_STATE_KEY},
            ).fetchone()
            return row[0] if row else None
    except Exception as exc:
        logger.warning("Could not load history state from database: %s", exc)
        return None


def save_history_id(history_id: str) -> None:
    """
    Persist the Gmail historyId to the database.

    Never regresses — a lower ID than the current stored value is silently
    ignored to prevent replaying already-processed messages.
    """
    current = load_history_id()
    if current is not None:
        try:
            if int(history_id) <= int(current):
                return
        except ValueError:
            pass
    try:
        _upsert(_get_engine(), _HISTORY_STATE_KEY, history_id)
    except Exception as exc:
        logger.warning("Could not save history state to database: %s", exc)
