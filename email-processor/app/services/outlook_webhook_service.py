"""Outlook Microsoft Graph webhook subscription and notification handling."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.config import get_settings
from app.db import crud
from app.db.database import get_session_factory
from app.utils import is_enabled_flag
from app.services.outlook_inbound_service import (
    _get_access_token,
    _load_connection_fields,
    _save_connection_fields,
    _to_graph_time,
    _parse_iso_datetime,
    sync_outlook_connection,
)

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MAX_SUBSCRIPTION_MINUTES = 4230
DEFAULT_SUBSCRIPTION_MINUTES = 2880


def _list_inbound_outlook_ids(include_disabled: bool = False) -> list[str]:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        return [
            c.id
            for c in crud.get_connections(db)
            if c.direction == "inbound"
            and c.type in {"outlook", "outlook365"}
            and (include_disabled or is_enabled_flag((c.fields or {}).get("enabled", True)))
        ]


def _subscription_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _subscription_urls(fields: dict[str, Any]) -> tuple[str, str]:
    settings = get_settings()
    notification_url = (
        str(fields.get("webhook_notification_url") or settings.outlook_webhook_notification_url or "").strip()
    )
    lifecycle_url = (
        str(fields.get("webhook_lifecycle_url") or settings.outlook_webhook_lifecycle_url or "").strip()
    )
    if not lifecycle_url:
        lifecycle_url = notification_url
    if not notification_url:
        raise RuntimeError(
            "No Outlook webhook notification URL configured. Set OUTLOOK_WEBHOOK_NOTIFICATION_URL or connection field webhook_notification_url."
        )
    return notification_url, lifecycle_url


def _desired_expiration() -> str:
    minutes = min(MAX_SUBSCRIPTION_MINUTES, DEFAULT_SUBSCRIPTION_MINUTES)
    return _to_graph_time(datetime.now(timezone.utc) + timedelta(minutes=minutes))


def _needs_renewal(expires_at: str | None) -> bool:
    renew_before = max(5, int(get_settings().outlook_webhook_renew_before_minutes))
    exp_dt = _parse_iso_datetime(expires_at)
    if not exp_dt:
        return True
    return exp_dt <= (datetime.now(timezone.utc) + timedelta(minutes=renew_before))


def _client_state(fields: dict[str, Any]) -> str:
    configured = get_settings().outlook_webhook_client_state_secret.strip()
    if configured:
        fields["_outlook_webhook_client_state"] = configured
        return configured
    existing = str(fields.get("_outlook_webhook_client_state") or "").strip()
    if existing:
        return existing
    generated = secrets.token_urlsafe(24)
    fields["_outlook_webhook_client_state"] = generated
    return generated


def _renew_subscription(access_token: str, subscription_id: str) -> dict[str, Any]:
    payload = {"expirationDateTime": _desired_expiration()}
    resp = requests.patch(
        f"{GRAPH_BASE}/subscriptions/{subscription_id}",
        headers=_subscription_headers(access_token),
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Failed to renew Outlook subscription ({resp.status_code}): {resp.text[:300]}")
    return dict(resp.json() or {})


def _create_subscription(conn_id: str, access_token: str, fields: dict[str, Any]) -> dict[str, Any]:
    notification_url, lifecycle_url = _subscription_urls(fields)
    client_state = _client_state(fields)

    payload = {
        "changeType": "created",
        "notificationUrl": notification_url,
        "resource": "/me/mailFolders('Inbox')/messages",
        "expirationDateTime": _desired_expiration(),
        "clientState": client_state,
        "latestSupportedTlsVersion": "v1_2",
    }
    if lifecycle_url:
        payload["lifecycleNotificationUrl"] = lifecycle_url

    resp = requests.post(
        f"{GRAPH_BASE}/subscriptions",
        headers=_subscription_headers(access_token),
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Failed to create Outlook subscription for '{conn_id}' ({resp.status_code}): {resp.text[:300]}")
    return dict(resp.json() or {})


def ensure_outlook_subscription(conn_id: str) -> dict[str, Any]:
    """Ensure one active Graph webhook subscription exists for this Outlook inbound connection.

    If no webhook notification URL is configured (neither the env var nor a connection
    field), the function returns immediately with ``status='skipped'`` so that
    polling-only deployments are not blocked.
    """
    _conn_type, fields = _load_connection_fields(conn_id)
    if not is_enabled_flag(fields.get("enabled", True)):
        raise RuntimeError(f"Connection '{conn_id}' is disabled.")

    # Skip webhook setup when no public notification URL is available.
    settings = get_settings()
    notification_url = str(
        fields.get("webhook_notification_url") or settings.outlook_webhook_notification_url or ""
    ).strip()
    if not notification_url:
        logger.info(
            "No OUTLOOK_WEBHOOK_NOTIFICATION_URL configured for '%s' — skipping Graph subscription setup. "
            "Emails will be delivered via polling only.",
            conn_id,
        )
        return {"connection_id": conn_id, "status": "skipped", "reason": "no_webhook_url"}

    prior_cache = fields.get("_msal_cache")
    token, fields = _get_access_token(conn_id, fields)

    sub_id = str(fields.get("_outlook_subscription_id") or "").strip()
    sub_exp = str(fields.get("_outlook_subscription_expiration") or "").strip()

    if sub_id and not _needs_renewal(sub_exp):
        # Only persist if the MSAL token cache was refreshed.
        if fields.get("_msal_cache") != prior_cache:
            _save_connection_fields(conn_id, fields)
        return {"connection_id": conn_id, "status": "ok", "subscription_id": sub_id, "expires_at": sub_exp, "action": "noop"}

    data: dict[str, Any]
    action = "create"
    if sub_id:
        try:
            data = _renew_subscription(token, sub_id)
            action = "renew"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Renew failed for %s (%s), creating new subscription.", conn_id, exc)
            data = _create_subscription(conn_id, token, fields)
            action = "create"
    else:
        data = _create_subscription(conn_id, token, fields)

    fields["_outlook_subscription_id"] = data.get("id") or sub_id
    fields["_outlook_subscription_expiration"] = data.get("expirationDateTime") or _desired_expiration()
    fields["_outlook_subscription_resource"] = data.get("resource") or "/me/mailFolders('Inbox')/messages"
    _save_connection_fields(conn_id, fields)

    return {
        "connection_id": conn_id,
        "status": "ok",
        "subscription_id": fields.get("_outlook_subscription_id"),
        "expires_at": fields.get("_outlook_subscription_expiration"),
        "action": action,
    }


def ensure_all_outlook_subscriptions() -> dict[str, Any]:
    ok: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for conn_id in _list_inbound_outlook_ids():
        try:
            ok.append(ensure_outlook_subscription(conn_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Outlook subscription renewal failed for '%s': %s", conn_id, exc)
            errors.append({"connection_id": conn_id, "error": str(exc)})

    return {"processed_connections": len(ok), "errors": errors, "details": ok}


def _build_subscription_map() -> dict[str, tuple[str, dict[str, Any]]]:
    """Return {subscription_id: (conn_id, fields)} for all inbound Outlook connections (one DB query)."""
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        result: dict[str, tuple[str, dict[str, Any]]] = {}
        for c in crud.get_connections(db):
            if c.direction != "inbound" or c.type not in {"outlook", "outlook365"}:
                continue
            fields = dict(c.fields or {})
            sub_id = str(fields.get("_outlook_subscription_id") or "").strip()
            if sub_id:
                result[sub_id] = (c.id, fields)
        return result


def process_outlook_notifications(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle Graph webhook notifications and trigger sync for affected connections."""
    values = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return {"status": "ignored", "reason": "payload has no value array"}

    # Build a subscription-id → (conn_id, fields) map with a single DB query.
    subscription_map = _build_subscription_map()

    touched: set[str] = set()
    renewals = 0
    unknown_subscriptions = 0
    invalid_client_state = 0
    errors: list[dict[str, str]] = []

    for item in values:
        if not isinstance(item, dict):
            continue
        sub_id = str(item.get("subscriptionId") or "").strip()
        if not sub_id:
            continue

        mapping = subscription_map.get(sub_id)
        if not mapping:
            unknown_subscriptions += 1
            continue
        conn_id, fields = mapping

        if not is_enabled_flag(fields.get("enabled", True)):
            continue
        expected_client_state = str(fields.get("_outlook_webhook_client_state") or "").strip()
        actual_client_state = str(item.get("clientState") or "").strip()
        if expected_client_state and actual_client_state and expected_client_state != actual_client_state:
            invalid_client_state += 1
            logger.warning("Ignoring Outlook webhook for %s due to clientState mismatch.", conn_id)
            continue

        lifecycle_event = str(item.get("lifecycleEvent") or "").strip().lower()
        if lifecycle_event in {"reauthorizationrequired", "subscriptionremoved", "missed"}:
            try:
                ensure_outlook_subscription(conn_id)
                renewals += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({"connection_id": conn_id, "error": str(exc)})
                continue

        touched.add(conn_id)

    processed = 0
    for conn_id in touched:
        try:
            sync_outlook_connection(conn_id)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"connection_id": conn_id, "error": str(exc)})

    return {
        "status": "ok",
        "notifications_received": len(values),
        "connections_synced": processed,
        "subscriptions_renewed": renewals,
        "unknown_subscriptions": unknown_subscriptions,
        "invalid_client_state": invalid_client_state,
        "errors": errors,
    }
