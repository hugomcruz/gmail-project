import base64
import json
from typing import Any
from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Pub/Sub push message envelope
# ---------------------------------------------------------------------------

class PubSubMessage(BaseModel):
    """A single Pub/Sub message as delivered via push."""

    data: str  # base64-encoded payload
    messageId: str
    publishTime: str
    attributes: dict[str, str] = {}

    def decode_data(self) -> dict[str, Any]:
        """Decode the base64 payload and parse it as JSON."""
        raw = base64.b64decode(self.data).decode("utf-8")
        return json.loads(raw)


class PubSubEnvelope(BaseModel):
    """Outer wrapper sent by Pub/Sub push subscriptions."""

    message: PubSubMessage
    subscription: str


# ---------------------------------------------------------------------------
# Gmail history / watch models
# ---------------------------------------------------------------------------

class GmailNotification(BaseModel):
    """Decoded Gmail change notification embedded in a Pub/Sub message."""

    emailAddress: str
    historyId: str


class GmailWatchRequest(BaseModel):
    """Request body for starting a Gmail push watch."""

    topic_name: str | None = None  # overrides the value from settings when provided
    label_ids: list[str] = ["INBOX"]
    label_filter_action: str = "include"


class GmailWatchResponse(BaseModel):
    """Response returned to the caller after setting up a Gmail watch."""

    historyId: str
    expiration: str
