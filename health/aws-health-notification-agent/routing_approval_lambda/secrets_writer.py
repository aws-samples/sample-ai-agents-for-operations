# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Read-modify-write Secrets Manager for routing config updates."""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Routing JSON key → Secrets Manager key
_KEY_MAP: dict[str, str] = {
    "by_resource": "resource_team_map",
    "by_account": "account_team_map",
    "by_service": "service_team_map",
    "by_ou": "ou_team_map",
    "default": "default_assignee",
}


def merge_routing_into_secret(existing_secret: dict, routing_json: dict) -> dict:
    """Merge routing JSON into an existing secret dict (pure function).

    Maps routing keys to Secrets Manager keys:
      by_resource → resource_team_map
      by_account  → account_team_map
      by_service  → service_team_map
      by_ou       → ou_team_map
      default     → default_assignee

    All existing keys are preserved; only the routing keys are
    added or overwritten.
    """
    merged = dict(existing_secret)
    for routing_key, secret_key in _KEY_MAP.items():
        if routing_key in routing_json:
            merged[secret_key] = routing_json[routing_key]
    return merged


def update_routing_config(
    secret_arn: str,
    routing_json: dict,
) -> dict:
    """Read-modify-write the Jira secret in Secrets Manager.

    Merges service_team_map, ou_team_map, default_assignee.
    Preserves all other keys (jira_api_token, etc.).
    """
    region = os.environ.get("AWS_REGION", "eu-west-1")
    client = boto3.client("secretsmanager", region_name=region)

    logger.info(
        json.dumps(
            {
                "message": "Reading current secret",
                "secret_arn": secret_arn,
            }
        )
    )

    get_response = client.get_secret_value(SecretId=secret_arn)
    existing_secret = json.loads(get_response["SecretString"])

    updated_secret = merge_routing_into_secret(existing_secret, routing_json)

    logger.info(
        json.dumps(
            {
                "message": "Writing updated secret",
                "secret_arn": secret_arn,
                "service_team_map_count": len(updated_secret.get("service_team_map", {})),
                "ou_team_map_count": len(updated_secret.get("ou_team_map", {})),
            }
        )
    )

    client.put_secret_value(
        SecretId=secret_arn,
        SecretString=json.dumps(updated_secret),
    )

    return updated_secret
