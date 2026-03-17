"""JIRA service — create issues using the JIRA REST API v3."""

import io
import logging

from jira import JIRA, JIRAError

logger = logging.getLogger(__name__)

# Client cache keyed by (url, user)
_client_cache: dict[tuple, JIRA] = {}


def _get_client(jira_url: str, jira_user: str, jira_token: str) -> JIRA:
    cache_key = (jira_url, jira_user)
    if cache_key not in _client_cache:
        if not jira_url or not jira_user or not jira_token:
            raise ValueError(
                "JIRA connection not fully configured. "
                "Ensure 'url', 'user', and 'token' are set in connections.yaml."
            )
        _client_cache[cache_key] = JIRA(
            server=jira_url,
            basic_auth=(jira_user, jira_token),
        )
        logger.info("Connected to JIRA at %s", jira_url)
    return _client_cache[cache_key]


def create_issue(
    jira_url: str,
    jira_user: str,
    jira_token: str,
    summary: str,
    description: str,
    project: str = "",
    issue_type: str = "Task",
    labels: list[str] | None = None,
    priority: str = "Medium",
    attachments: list[dict] | None = None,
) -> str:
    """
    Create a JIRA issue and return its key (e.g. 'FIN-42').

    Args:
        jira_url:    JIRA base URL (from connection config).
        jira_user:   JIRA username / email (from connection config).
        jira_token:  JIRA API token (from connection config).
        summary:     Issue title.
        description: Issue body (plain text).
        project:     JIRA project key.
        issue_type:  Issue type name.
        labels:      List of label strings to attach.
        priority:    Priority name: Highest/High/Medium/Low/Lowest.
        attachments: Optional list of dicts with keys 'filename', 'data' (bytes),
                     and 'mime_type'. Each entry is uploaded as an issue attachment.

    Returns:
        The created issue key string.

    Raises:
        ValueError:  If project key is missing.
        JIRAError:   On JIRA API failure.
    """
    if not project:
        raise ValueError(
            "JIRA project not configured. "
            "Set 'project' or 'default_project' in the connection or rule action config."
        )

    client = _get_client(jira_url, jira_user, jira_token)

    fields: dict = {
        "project": {"key": project},
        "issuetype": {"name": issue_type},
        "summary": summary,
        "description": description,
        "priority": {"name": priority},
    }

    if labels:
        fields["labels"] = labels

    try:
        issue = client.create_issue(fields=fields)
        logger.debug("Created JIRA issue: %s — %s", issue.key, summary)
    except JIRAError as exc:
        logger.error("JIRA create_issue failed (status %s): %s", exc.status_code, exc.text)
        raise

    # Upload attachments
    if attachments:
        for att in attachments:
            filename = att.get("filename", "attachment")
            data = att.get("data", b"")
            try:
                client.add_attachment(
                    issue=issue.key,
                    attachment=io.BytesIO(data),
                    filename=filename,
                )
                logger.debug("Attached '%s' to JIRA issue %s", filename, issue.key)
            except JIRAError as exc:
                logger.error(
                    "Failed to attach '%s' to %s (status %s): %s",
                    filename, issue.key, exc.status_code, exc.text,
                )
                # Continue uploading remaining attachments even if one fails

    return issue.key
