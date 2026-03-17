"""
Rules engine — evaluates rules against emails.
Rules and connections are loaded from the database.
"""

import logging
from typing import Any

from app.rules import conditions as cond_module
from app.rules import actions as action_module
from app.rules.connections import ConnectionRegistry

logger = logging.getLogger(__name__)


class RulesEngine:
    def __init__(self) -> None:
        self.registry = ConnectionRegistry()
        self.rules: list[dict[str, Any]] = []
        self._db_mode = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Reload rules and connections from the database."""
        if self._db_mode:
            self._load_from_db()
            self._load_connections_from_db()

    def enable_db_mode(self) -> None:
        """Switch the engine to read rules and connections from the database."""
        self._db_mode = True
        self._load_from_db()
        self._load_connections_from_db()

    def _load_from_db(self) -> None:
        try:
            from app.db.database import get_session_factory
            from app.db.models import Rule
            SessionLocal = get_session_factory()
            with SessionLocal() as db:
                rows = db.query(Rule).order_by(Rule.id).all()
                self.rules = [r.to_engine_dict() for r in rows if r.enabled]
            logger.info("Loaded %d enabled rule(s) from database", len(self.rules))
        except Exception as exc:
            logger.error("Failed to load rules from DB: %s", exc)

    def _load_connections_from_db(self) -> None:
        try:
            from app.db.database import get_session_factory
            from app.db.models import Connection
            SessionLocal = get_session_factory()
            with SessionLocal() as db:
                rows = db.query(Connection).all()
                self.registry.reload_from_list([r.to_registry_dict() for r in rows])
        except Exception as exc:
            logger.error("Failed to load connections from DB: %s", exc)

    def reload_connections(self) -> None:
        """Reload connections from the database."""
        if self._db_mode:
            self._load_connections_from_db()

    def process(self, email: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Evaluate all rules against *email*.
        Returns a list of result dicts, one per matched rule.
        """
        results = []

        if not self.rules:
            logger.warning(
                "engine.process called but no rules are loaded (email id=%s)",
                email.get("id"),
            )
            return results

        logger.info(
            "Evaluating %d rule(s) against email id=%s | subject='%s'",
            len(self.rules), email.get("id"), email.get("subject", ""),
        )

        for rule in self.rules:
            name = rule.get("name", "<unnamed>")
            match_mode = rule.get("match", "all").lower()
            rule_conditions = rule.get("conditions", [])
            rule_actions = rule.get("actions", [])

            # ── Evaluate conditions ──────────────────────────────────────────
            evaluations = [cond_module.evaluate(c, email) for c in rule_conditions]

            if match_mode == "any":
                matched = any(evaluations)
            else:  # default: all
                matched = all(evaluations) if evaluations else False

            if not matched:
                logger.info(
                    "Rule '%s' did NOT match (mode=%s, results=%s, conditions=%s)",
                    name, match_mode, evaluations,
                    [c.get("type") + "=" + repr(c.get("value")) for c in rule_conditions],
                )
                continue

            logger.info("Rule '%s' matched email id=%s — running %d action(s)",
                        name, email.get("id"), len(rule_actions))

            # ── Execute actions ──────────────────────────────────────────────
            action_results = []
            for action in rule_actions:
                result = action_module.execute(action, email, self.registry)
                action_results.append(result)
                if result["status"] == "error":
                    logger.error("Action '%s' in rule '%s' errored: %s",
                                 action.get("type"), name, result.get("error"))

            results.append({
                "rule": name,
                "email_id": email.get("id"),
                "subject": email.get("subject"),
                "actions": action_results,
            })

        return results
