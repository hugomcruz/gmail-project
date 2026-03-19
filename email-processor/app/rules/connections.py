"""
Connection registry — holds named connections loaded from the database
and provides them to action executors by ID.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_FIELDS: dict[str, list[str]] = {
    "s3": ["bucket"],
    "jira": ["url", "user", "token"],
    "onedrive": [],  # client_id is configured server-side via ONEDRIVE_CLIENT_ID env var
    "mailgun": ["api_key", "domain", "sender_address"],
}


class ConnectionRegistry:
    def __init__(self) -> None:
        self._connections: dict[str, dict[str, Any]] = {}

    def get(self, connection_id: str) -> dict[str, Any]:
        """
        Return the connection config dict for *connection_id*.
        Raises KeyError if not found.
        """
        try:
            return self._connections[connection_id]
        except KeyError:
            available = list(self._connections.keys())
            raise KeyError(
                f"Connection '{connection_id}' not found. "
                f"Available: {available}"
            )

    def all_ids(self) -> list[str]:
        return list(self._connections.keys())

    def reload_from_list(self, conns: list[dict[str, Any]]) -> None:
        """Populate the registry from a list of flat dicts (e.g. loaded from the DB)."""
        loaded: dict[str, dict[str, Any]] = {}
        errors = 0
        for conn in conns:
            conn_id = conn.get("id", "").strip()
            conn_type = conn.get("type", "").strip()
            if not conn_id:
                errors += 1
                continue
            required = REQUIRED_FIELDS.get(conn_type, [])
            missing = [f for f in required if not conn.get(f)]
            if missing:
                logger.error(
                    "Connection '%s' (type=%s) missing required fields: %s — skipped.",
                    conn_id, conn_type, missing,
                )
                errors += 1
                continue
            loaded[conn_id] = conn
        self._connections = loaded
        logger.info(
            "Registry loaded %d connection(s) from database%s",
            len(loaded),
            f" ({errors} error(s) skipped)" if errors else "",
        )
