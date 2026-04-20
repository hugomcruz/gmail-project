"""
OneDrive for Business / SharePoint service — upload files using Microsoft Graph API.

Uses work/school (Azure AD) accounts via the /organizations authority.
Supports:
  - OneDrive for Business  (/me/drive  — default)
  - SharePoint document libraries  (site_url field on the connection)

Authentication uses the same MSAL device-code flow as OneDrive personal,
but the authority and scopes differ.
"""

import logging
import re
from typing import Any

import msal
import requests

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTHORITY = "https://login.microsoftonline.com/organizations"
# Files.ReadWrite.All covers both OneDrive for Business and SharePoint file
# access as a delegated permission, without requiring admin consent.
SCOPES = ["Files.ReadWrite.All"]
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB

_ILLEGAL_FILENAME_RE = re.compile(r'[/\\:|*?"<>]')


def _sanitize_filename(name: str) -> str:
    return _ILLEGAL_FILENAME_RE.sub("-", name)


def get_authority(tenant_id: str = "") -> str:
    """Return the MSAL authority URL, using a specific tenant when supplied."""
    if tenant_id:
        return f"https://login.microsoftonline.com/{tenant_id}"
    return AUTHORITY


def _get_token(cache_data: str | None, client_id: str, tenant_id: str = "") -> tuple[str, str | None]:
    """
    Return a valid access token, refreshing via MSAL if needed.
    Returns (access_token, updated_cache_data_or_None).
    Raises RuntimeError if no valid account exists.
    """
    cache = msal.SerializableTokenCache()
    if cache_data:
        cache.deserialize(cache_data)

    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=get_authority(tenant_id),
        token_cache=cache,
    )

    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result or "access_token" not in result:
        raise RuntimeError(
            "OneDrive 365 token not available or expired. "
            "Re-authenticate via the Connections page in the UI."
        )

    updated = cache.serialize() if cache.has_state_changed else None
    return result["access_token"], updated


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _resolve_drive_root(token: str, site_url: str) -> str:
    """
    Return the Graph API base path for the target drive.
    - No site_url → OneDrive for Business: /me/drive/root:/
    - site_url provided → look up the SharePoint site and return its default drive root.
      Requires the Azure app to have Sites.ReadWrite.All delegated permission granted
      (may need tenant admin consent). Raises RuntimeError with a clear message on 403.
    """
    if not site_url:
        return f"{GRAPH_BASE}/me/drive/root:"

    # site_url can be a full URL like https://tenant.sharepoint.com/sites/MySite
    site_url = site_url.rstrip("/")
    try:
        from urllib.parse import urlparse
        parsed = urlparse(site_url)
        hostname = parsed.netloc
        path = parsed.path.rstrip("/") or "/"
        site_resp = requests.get(
            f"{GRAPH_BASE}/sites/{hostname}:{path}",
            headers=_headers(token),
            timeout=20,
        )
        if site_resp.status_code == 403:
            raise RuntimeError(
                f"Access denied resolving SharePoint site '{site_url}'. "
                "SharePoint site lookup requires the 'Sites.ReadWrite.All' delegated "
                "permission, which may need tenant admin consent. "
                "Ask your Microsoft 365 admin to grant that permission to the Azure app, "
                "then re-authenticate the connection."
            )
        _raise_for_status(site_resp, "resolve SharePoint site")
        site_id = site_resp.json()["id"]
        return f"{GRAPH_BASE}/sites/{site_id}/drive/root:"
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Could not resolve SharePoint site '{site_url}': {exc}") from exc


def upload_bytes(
    data: bytes,
    remote_path: str,
    content_type: str = "application/octet-stream",
    folder: str | None = None,
    client_id: str = "",
    tenant_id: str = "",
    site_url: str = "",
    token_cache_data: str | None = None,
) -> tuple[str, str | None]:
    """
    Upload raw bytes to OneDrive for Business or SharePoint.

    Returns (web_url, updated_cache_data_or_None).
    """
    if not client_id:
        raise ValueError(
            "OneDrive 365 client_id not configured. Set AZURE_CLIENT_ID in the server environment."
        )

    token, updated_cache = _get_token(token_cache_data, client_id, tenant_id)
    drive_root = _resolve_drive_root(token, site_url)

    safe_filename = _sanitize_filename(remote_path.lstrip("/"))
    path = safe_filename
    if folder:
        path = f"{folder.rstrip('/')}/{safe_filename}"

    logger.debug("Uploading %d bytes to OneDrive 365 path '%s'", len(data), path)

    if len(data) <= CHUNK_SIZE:
        url = _simple_upload(token, drive_root, path, data, content_type)
    else:
        url = _resumable_upload(token, drive_root, path, data, content_type)

    logger.debug("OneDrive 365 upload complete: %s", url)
    return url, updated_cache


def _simple_upload(token: str, drive_root: str, path: str, data: bytes, content_type: str) -> str:
    endpoint = f"{drive_root}/{path}:/content"
    resp = requests.put(
        endpoint,
        headers={**_headers(token), "Content-Type": content_type},
        data=data,
        timeout=60,
    )
    _raise_for_status(resp, "simple upload")
    return resp.json().get("webUrl", endpoint)


def _resumable_upload(token: str, drive_root: str, path: str, data: bytes, content_type: str) -> str:
    session_url = f"{drive_root}/{path}:/createUploadSession"
    session_resp = requests.post(
        session_url,
        headers={**_headers(token), "Content-Type": "application/json"},
        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        timeout=30,
    )
    _raise_for_status(session_resp, "create upload session")
    upload_url = session_resp.json()["uploadUrl"]

    total = len(data)
    offset = 0
    web_url = upload_url

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
            "OneDrive 365 API error during %s: HTTP %d — %s",
            context, resp.status_code, resp.text[:300],
        )
        resp.raise_for_status()
