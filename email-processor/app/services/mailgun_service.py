"""
Mailgun service — forward emails via the Mailgun Messages API.

The forwarded message:
  - From:    <original sender display name> <heat@berzuk.com>
  - To:      configured recipient(s)
  - Subject: original subject (optionally prefixed)
  - Body:    original plain-text body (HTML if available)
  - Attachments: all original attachments re-attached
"""

import logging
from email.utils import formataddr, parseaddr

import requests

logger = logging.getLogger(__name__)

MAILGUN_US_BASE = "https://api.mailgun.net/v3"
MAILGUN_EU_BASE = "https://api.eu.mailgun.net/v3"


def forward_email(
    *,
    api_key: str,
    domain: str,
    sender_address: str,
    to: list[str],
    original_email: dict,
    subject_prefix: str = "",
    api_base: str = MAILGUN_EU_BASE,
) -> str:
    """
    Forward an email through Mailgun.

    Args:
        api_key:        Mailgun private API key (from connection config).
        domain:         Mailgun sending domain, e.g. 'berzuk.com'.
        sender_address: Fixed From address, e.g. 'heat@berzuk.com'.
        to:             List of recipient addresses.
        original_email: Full email dict as published to NATS.
        subject_prefix: Optional prefix prepended to the subject, e.g. 'Fwd: '.

    Returns:
        The Mailgun message ID from the API response.

    Raises:
        requests.HTTPError: On Mailgun API failure.
    """
    # Preserve the original sender's display name, swap the address
    raw_from = original_email.get("from", "")
    display_name, _ = parseaddr(raw_from)
    from_header = formataddr((display_name or raw_from, sender_address))

    subject = original_email.get("subject", "(no subject)")
    if subject_prefix:
        subject = f"{subject_prefix}{subject}"

    body_plain = original_email.get("body_plain", "")
    body_html = original_email.get("body_html", "")

    url = f"{api_base}/{domain}/messages"

    data: dict = {
        "from": from_header,
        "to": to,
        "subject": subject,
        "text": body_plain,
    }
    if body_html:
        data["html"] = body_html

    # Build multipart files list: (field_name, (filename, bytes, mime_type))
    files: list = []
    for att in original_email.get("attachments", []):
        import base64
        filename = att.get("filename", "attachment")
        mime_type = att.get("mimeType", "application/octet-stream")
        data_b64 = att.get("data_base64", "")
        try:
            raw = base64.urlsafe_b64decode(data_b64 + "==")
        except Exception as exc:
            logger.error("Could not decode attachment '%s': %s", filename, exc)
            continue
        files.append(("attachment", (filename, raw, mime_type)))

    resp = requests.post(
        url,
        auth=("api", api_key),
        data=data,
        files=files if files else None,
        timeout=30,
    )

    if not resp.ok:
        logger.error(
            "Mailgun API error: HTTP %d — %s", resp.status_code, resp.text[:300]
        )
        resp.raise_for_status()

    message_id = resp.json().get("id", "")
    logger.debug(
        "Mailgun forwarded '%s' to %s — message id: %s", subject, to, message_id
    )
    return message_id
