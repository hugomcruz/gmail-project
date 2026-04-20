"""
Condition evaluators for the rules engine.

Each condition is a dict with a `type` key and optional parameters.
The `evaluate(condition, email)` function dispatches to the appropriate handler.

Supported condition types:
  from_equals          exact match on From header
  from_contains        substring match on From header
  to_contains          substring match on To header
  subject_equals       exact match on Subject header
  subject_contains     substring match on Subject header
  subject_starts_with  Subject starts with value
  subject_ends_with    Subject ends with value
  body_contains        substring match on plain body
  has_attachments      email has ≥ 1 attachment
  attachment_count_gte email has ≥ N attachments
  label_contains       labelIds list contains value
    source_connection_equals inbound source connection ID equals value
    source_provider_equals   inbound source provider equals value
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def evaluate(condition: dict[str, Any], email: dict[str, Any]) -> bool:
    """Dispatch a single condition dict against an email. Returns True/False."""
    ctype = condition.get("type", "")
    raw_val = condition.get("value")
    # Guard against None stored in DB (str(None) == "None" would never match)
    value = str(raw_val).strip() if raw_val is not None else ""
    case_sensitive = condition.get("case_sensitive", False)

    try:
        return _HANDLERS[ctype](condition, email, value, case_sensitive)
    except KeyError:
        logger.warning("Unknown condition type '%s' — treating as False", ctype)
        return False
    except Exception as exc:
        logger.error("Error evaluating condition '%s': %s", ctype, exc)
        return False


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _norm(s: str, case_sensitive: bool) -> str:
    return s if case_sensitive else s.lower()


def _from_equals(c, email, value, cs):
    return _norm(email.get("from", ""), cs) == _norm(value, cs)


def _from_contains(c, email, value, cs):
    return _norm(value, cs) in _norm(email.get("from", ""), cs)


def _to_contains(c, email, value, cs):
    return _norm(value, cs) in _norm(email.get("to", ""), cs)


def _subject_equals(c, email, value, cs):
    return _norm(email.get("subject", ""), cs) == _norm(value, cs)


def _subject_contains(c, email, value, cs):
    return _norm(value, cs) in _norm(email.get("subject", ""), cs)


def _subject_starts_with(c, email, value, cs):
    return _norm(email.get("subject", ""), cs).startswith(_norm(value, cs))


def _subject_ends_with(c, email, value, cs):
    return _norm(email.get("subject", ""), cs).endswith(_norm(value, cs))


def _body_contains(c, email, value, cs):
    return _norm(value, cs) in _norm(email.get("body_plain", ""), cs)


def _has_attachments(c, email, value, cs):
    return len(email.get("attachments", [])) > 0


def _attachment_count_gte(c, email, value, cs):
    try:
        return len(email.get("attachments", [])) >= int(value)
    except ValueError:
        logger.warning("attachment_count_gte: invalid value '%s'", value)
        return False


def _label_contains(c, email, value, cs):
    labels = [_norm(lbl, cs) for lbl in email.get("labelIds", [])]
    return _norm(value, cs) in labels


def _source_connection_equals(c, email, value, cs):
    src = str(email.get("source_connection") or email.get("connection_id") or "")
    return _norm(src, cs) == _norm(value, cs)


def _source_provider_equals(c, email, value, cs):
    src = str(email.get("source_provider") or email.get("provider") or "")
    return _norm(src, cs) == _norm(value, cs)


_HANDLERS = {
    "from_equals": _from_equals,
    "from_contains": _from_contains,
    "to_contains": _to_contains,
    "subject_equals": _subject_equals,
    "subject_contains": _subject_contains,
    "subject_starts_with": _subject_starts_with,
    "subject_ends_with": _subject_ends_with,
    "body_contains": _body_contains,
    "has_attachments": _has_attachments,
    "attachment_count_gte": _attachment_count_gte,
    "label_contains": _label_contains,
    "source_connection_equals": _source_connection_equals,
    "source_provider_equals": _source_provider_equals,
}
