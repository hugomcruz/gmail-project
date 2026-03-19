"""
OneDrive Personal service — upload files using Microsoft Graph API.

Authentication uses MSAL with a persistent token cache stored in
`onedrive_token_cache.json`.  Run `setup_onedrive.py` once to sign in
via device code flow (no browser redirect needed).  After that, tokens
are refreshed automatically.

Upload strategy:
  - Files ≤ 4 MB  →  single PUT (simple upload)
  - Files  > 4 MB →  upload session (resumable, chunked)
"""

import logging
import re
from typing import Any

import msal
import requests

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["Files.ReadWrite"]
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB

# Characters that are illegal in OneDrive/SharePoint filenames or that the
# Graph API interprets as path separators.  Replace them with a safe substitute.
_ILLEGAL_FILENAME_RE = re.compile(r'[/\\:|*?"<>]')


def _sanitize_filename(name: str) -> str:
    """Replace characters that are illegal or path-significant in OneDrive filenames."""
    return _ILLEGAL_FILENAME_RE.sub("-", name)


def _get_token(cache_data: str | None, client_id: str) -> tuple[str, str | None]:
    """
    Return a valid access token, refreshing via MSAL if needed.
    Takes the serialised MSAL token cache as a string (or None/empty if no
    token has been saved yet).
    Returns (access_token, updated_cache_data_or_None).
    updated_cache_data is non-None only when the cache was refreshed and the
    caller should persist the new value.
    Raises RuntimeError if no valid account exists in the cache.
    """
    cache = msal.SerializableTokenCache()

    if cache_data:
        cache.deserialize(cache_data)

    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=AUTHORITY,
        token_cache=cache,
    )

    accounts = app.get_accounts()
    result = None

    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result or "access_token" not in result:
        raise RuntimeError(
            "OneDrive token not available or expired. "
            "Re-authenticate via the Connections page in the UI."
        )

    updated = cache.serialize() if cache.has_state_changed else None
    return result["access_token"], updated


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def upload_bytes(
    data: bytes,
    remote_path: str,
    content_type: str = "application/octet-stream",
    folder: str | None = None,
    client_id: str = "",
    token_cache_data: str | None = None,
) -> tuple[str, str | None]:
    """
    Upload raw bytes to OneDrive Personal.

    Args:
        data:             File content as bytes.
        remote_path:      Destination filename in OneDrive.
        content_type:     MIME type of the file.
        folder:           Optional folder prefix from rule config.
        client_id:        Azure app client ID (from connection config).
        token_cache_data: Serialised MSAL token cache string (stored in DB).

    Returns:
        Tuple of (web_url, updated_cache_data_or_None).
        updated_cache_data is non-None when the token was silently refreshed
        and the caller should save the new value back to the DB.
    """
    if not client_id:
        raise ValueError(
            "OneDrive client_id not configured. "
            "Set 'client_id' in the connection definition."
        )

    token, updated_cache = _get_token(token_cache_data, client_id)

    # Sanitize the filename to remove characters that OneDrive interprets as
    # path separators or that are illegal in filenames (e.g. "FT/2014.pdf" → "FT-2014.pdf").
    safe_filename = _sanitize_filename(remote_path.lstrip("/"))

    # Build full remote path
    path = safe_filename
    if folder:
        path = f"{folder.rstrip('/')}/{safe_filename}"

    logger.debug("Uploading %d bytes to OneDrive path '%s'", len(data), path)

    if len(data) <= CHUNK_SIZE:
        url = _simple_upload(token, path, data, content_type)
    else:
        url = _resumable_upload(token, path, data, content_type)

    logger.debug("OneDrive upload complete: %s", url)
    return url, updated_cache


# ---------------------------------------------------------------------------
# Simple upload (≤ 4 MB)
# ---------------------------------------------------------------------------

def _simple_upload(token: str, path: str, data: bytes, content_type: str) -> str:
    endpoint = f"{GRAPH_BASE}/me/drive/root:/{path}:/content"
    resp = requests.put(
        endpoint,
        headers={**_headers(token), "Content-Type": content_type},
        data=data,
        timeout=60,
    )
    _raise_for_status(resp, "simple upload")
    return resp.json().get("webUrl", endpoint)


# ---------------------------------------------------------------------------
# Resumable upload (> 4 MB)
# ---------------------------------------------------------------------------

def _resumable_upload(token: str, path: str, data: bytes, content_type: str) -> str:
    # 1. Create upload session
    session_url = f"{GRAPH_BASE}/me/drive/root:/{path}:/createUploadSession"
    session_resp = requests.post(
        session_url,
        headers={**_headers(token), "Content-Type": "application/json"},
        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        timeout=30,
    )
    _raise_for_status(session_resp, "create upload session")
    upload_url = session_resp.json()["uploadUrl"]

    # 2. Upload in chunks
    total = len(data)
    offset = 0
    web_url = upload_url  # fallback

    while offset < total:
        chunk = data[offset: offset + CHUNK_SIZE]
        end = offset + len(chunk) - 1
        resp = requests.put(
            upload_url,
            headers={
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{end}/{total}",
                "Content-Type": content_type,
            },
            data=chunk,
            timeout=120,
        )
        _raise_for_status(resp, f"upload chunk {offset}-{end}")
        if resp.status_code in (200, 201):
            web_url = resp.json().get("webUrl", web_url)
        offset += len(chunk)

    return web_url


def _raise_for_status(resp: requests.Response, context: str) -> None:
    if not resp.ok:
        logger.error(
            "OneDrive API error during %s: HTTP %d — %s",
            context, resp.status_code, resp.text[:300],
        )
        resp.raise_for_status()
