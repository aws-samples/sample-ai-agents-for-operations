# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Jira REST API client for creating issues from health notifications."""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Optional
from typing_extensions import TypedDict
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import boto3

logger = logging.getLogger(__name__)


class JiraApiError(Exception):
    """Raised when the Jira API returns a non-2xx response."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Jira API error {status_code}: {message}")


class JiraConfig(TypedDict):
    base_url: str
    project_key: str
    issue_type: str
    user_email: str
    secret_arn: str
    default_assignee: str
    service_team_map: dict[str, str]
    ou_team_map: dict[str, str]


class JiraClient:
    """Authenticated Jira REST API v2 client."""

    def __init__(self, base_url: str, user_email: str, api_token: str):
        if not base_url.startswith("https://"):
            raise ValueError(f"Jira base URL must use HTTPS scheme, got: {base_url[:20]}")
        self._base_url = base_url.rstrip("/")
        self._auth_header = "Basic " + base64.b64encode(
            f"{user_email}:{api_token}".encode()
        ).decode()

    @staticmethod
    def from_config(config: JiraConfig) -> JiraClient:
        """Factory: retrieve API token from Secrets Manager, return configured client."""
        region = os.environ.get("AWS_REGION", "eu-west-1")
        sm = boto3.client("secretsmanager", region_name=region)
        secret_value = sm.get_secret_value(SecretId=config["secret_arn"])
        secret = json.loads(secret_value["SecretString"])
        api_token = secret.get("jira_api_token") or secret.get("Jira_API_TOKEN") or secret.get("api_token", "")

        # Merge team mappings from secret if present
        if "service_team_map" in secret:
            config["service_team_map"] = secret["service_team_map"]
        if "ou_team_map" in secret:
            config["ou_team_map"] = secret["ou_team_map"]
        if "resource_team_map" in secret:
            config["resource_team_map"] = secret["resource_team_map"]
        if "account_team_map" in secret:
            config["account_team_map"] = secret["account_team_map"]
        if "default_assignee" in secret:
            config["default_assignee"] = secret["default_assignee"]

        return JiraClient(config["base_url"], config["user_email"], api_token)

    def create_issue(self, fields: dict) -> dict:
        """POST /rest/api/2/issue. Returns the created issue dict."""
        url = f"{self._base_url}/rest/api/2/issue"
        data = json.dumps(fields).encode()
        req = Request(url, data=data, method="POST", headers={
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urlopen(req, timeout=30) as resp:  # nosec B310 # nosemgrep: dynamic-urllib-use-detected
                return json.loads(resp.read())
        except HTTPError as exc:
            body = exc.read().decode() if exc.fp else ""
            raise JiraApiError(exc.code, body) from exc

    def search_issues(self, jql: str, fields: list[str] | None = None, max_results: int = 5) -> list[dict]:
        """GET /rest/api/2/search. Returns list of issue dicts."""
        from urllib.parse import quote
        params = f"jql={quote(jql)}&maxResults={max_results}"
        if fields:
            params += f"&fields={','.join(fields)}"
        url = f"{self._base_url}/rest/api/2/search?{params}"
        req = Request(url, method="GET", headers={
            "Authorization": self._auth_header,
            "Accept": "application/json",
        })
        try:
            with urlopen(req, timeout=30) as resp:  # nosec B310 # nosemgrep: dynamic-urllib-use-detected
                data = json.loads(resp.read())
                return data.get("issues", [])
        except HTTPError as exc:
            body = exc.read().decode() if exc.fp else ""
            raise JiraApiError(exc.code, body) from exc

    def find_duplicate(self, project_key: str, event_arn_label: str) -> str | None:
        """Search for open issues with matching event ARN label.

        Returns issue key if found, None otherwise.
        Logs warning and returns None on search failure.
        """
        from aha_eventbridge_lambda.ticket_mapper import _sanitize_label
        label = _sanitize_label(event_arn_label)
        jql = f'project = "{project_key}" AND labels = "{label}" AND status != Done'
        try:
            issues = self.search_issues(jql, fields=["key"], max_results=1)
            if issues:
                key = issues[0].get("key", "")
                logger.info(json.dumps({"message": "Duplicate Jira ticket found", "issue_key": key, "event_arn": event_arn_label}))
                return key
            return None
        except Exception:
            logger.warning(json.dumps({"message": "Jira duplicate check failed, proceeding with creation", "event_arn": event_arn_label}))
            return None
