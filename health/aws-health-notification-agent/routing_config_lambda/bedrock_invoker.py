# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Amazon Bedrock model invocation and response parsing.

AI Security Controls:
- Input validation: File extension filtering (csv/json/txt only) in s3_reader.py
  before content reaches this module. Document size limited by S3 event payload.
- Prompt injection prevention: CONFIGURATION_PARSER_PROMPT uses structured
  instructions with explicit output format constraints (JSON-only response).
- Output validation: validate_routing_json() enforces strict schema (required keys,
  non-empty string values, correct types) before any routing config is applied.
- Human review: When REQUIRE_ROUTING_APPROVAL=true (default), LLM-generated
  routing configs require Slack approval before persisting to Secrets Manager.
- Error handling: Retries once with error-correction prompt on validation failure;
  raises ValueError if both attempts fail (no partial/invalid configs applied).
"""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

CONFIGURATION_PARSER_PROMPT = """You are a configuration parser. Given the following routing document, extract team routing \
assignments and produce a JSON object with exactly five keys:

- "by_resource": an object mapping specific resource names (e.g., cluster names like "my-cluster-3") to Jira Team IDs (UUIDs). Resource-level assignments have the HIGHEST priority.
- "by_account": an object mapping AWS account IDs (e.g., "111122223333") to Jira Team IDs (UUIDs). Account-level assignments have the second highest priority.
- "by_service": an object mapping AWS service names (e.g., "EKS", "RDS") to Jira Team IDs (UUIDs).
- "by_ou": an object mapping AWS Organization OU names or paths to Jira Team IDs (UUIDs).
- "default": a single Jira Team ID (UUID) to use when no other mapping matches.

Rules:
- The document may define teams with names AND their Jira Team IDs (UUIDs). ALWAYS use the Jira Team ID (UUID format like "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx") as the value, NOT the team name.
- Service names should be uppercase AWS service abbreviations (e.g., "EKS", "RDS", "EC2")
- Account IDs should be 12-digit AWS account IDs as strings
- Resource names should match the actual resource identifiers (cluster names, instance IDs, etc.)
- All values must be non-empty strings (Jira Team ID UUIDs)
- If a mapping type has no entries, use an empty object {{}}
- Return ONLY the JSON object, no markdown, no explanation

Document content:
{document_content}"""

ERROR_CORRECTION_PROMPT = """
The previous response was not valid JSON or was missing required keys.
Please try again. Return ONLY a valid JSON object with keys: by_resource, by_account, by_service, by_ou, default.
Previous response: {previous_response}"""

REQUIRED_KEYS = {"by_resource", "by_account", "by_service", "by_ou", "default"}


def validate_routing_json(data: dict) -> bool:
    """Validate that data contains required keys with correct types.

    Returns True iff data contains the keys ``by_resource`` (dict[str, str]),
    ``by_account`` (dict[str, str]), ``by_service`` (dict[str, str]),
    ``by_ou`` (dict[str, str]), and ``default`` (str),
    with all values being non-empty strings in the leaf mappings.
    """
    if not isinstance(data, dict):
        return False

    if not REQUIRED_KEYS.issubset(set(data.keys())):
        return False

    default = data["default"]
    if not isinstance(default, str) or not default:
        return False

    for key in ("by_resource", "by_account", "by_service", "by_ou"):
        mapping = data[key]
        if not isinstance(mapping, dict):
            return False
        for k, v in mapping.items():
            if not isinstance(k, str) or not k:
                return False
            if not isinstance(v, str) or not v:
                return False

    return True


def _parse_bedrock_response(response: dict) -> dict | None:
    """Extract and parse JSON from a Bedrock InvokeModel response.

    Returns the parsed dict or None if parsing fails.
    """
    try:
        body = json.loads(response["body"].read())
        # Claude Messages API response format
        text = body["content"][0]["text"]
        return json.loads(text)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        logger.warning(
            json.dumps(
                {
                    "message": "Failed to parse Amazon Bedrock response",
                    "error": str(exc),
                }
            )
        )
        return None


def invoke_bedrock(
    document_content: str,
    model_id: str = "eu.anthropic.claude-sonnet-4-6",
) -> dict:
    """Invoke Amazon Bedrock Claude to generate Routing JSON from document content.

    Returns parsed dict with by_service, by_ou, default keys.
    Retries once with error-correction prompt on validation failure.
    """
    # Required IAM permissions (defined in aha_eventbridge_lambda/template.yaml):
    # {
    #   "Effect": "Allow",
    #   "Action": "bedrock:InvokeModel",
    #   "Resource": [
    #     "arn:aws:bedrock:*::foundation-model/*",
    #     "arn:aws:bedrock:*:${AccountId}:inference-profile/*"
    #   ]
    # }
    region = os.environ.get("AWS_REGION", "eu-west-1")
    client = boto3.client("bedrock-runtime", region_name=region)

    prompt = CONFIGURATION_PARSER_PROMPT.format(document_content=document_content)

    request_body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

    logger.info(
        json.dumps(
            {
                "message": "Invoking Amazon Bedrock model",
                "model_id": model_id,
            }
        )
    )

    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=request_body,
    )

    parsed = _parse_bedrock_response(response)

    if parsed is not None and validate_routing_json(parsed):
        logger.info(
            json.dumps(
                {
                    "message": "Amazon Bedrock returned valid routing JSON",
                    "service_count": len(parsed["by_service"]),
                    "ou_count": len(parsed["by_ou"]),
                }
            )
        )
        return parsed

    # First attempt failed validation — retry with error-correction prompt
    previous_response = json.dumps(parsed) if parsed is not None else "(unparseable)"
    retry_prompt = prompt + ERROR_CORRECTION_PROMPT.format(
        previous_response=previous_response,
    )

    logger.warning(
        json.dumps(
            {
                "message": "First Amazon Bedrock response failed validation, retrying",
                "previous_response": previous_response,
            }
        )
    )

    retry_body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": retry_prompt}],
        }
    )

    retry_response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=retry_body,
    )

    retry_parsed = _parse_bedrock_response(retry_response)

    if retry_parsed is not None and validate_routing_json(retry_parsed):
        logger.info(
            json.dumps(
                {
                    "message": "Amazon Bedrock retry returned valid routing JSON",
                    "service_count": len(retry_parsed["by_service"]),
                    "ou_count": len(retry_parsed["by_ou"]),
                }
            )
        )
        return retry_parsed

    # Both attempts failed
    logger.error(
        json.dumps(
            {
                "message": "Amazon Bedrock retry also failed validation",
                "retry_response": json.dumps(retry_parsed) if retry_parsed is not None else "(unparseable)",
            }
        )
    )
    raise ValueError(
        "Amazon Bedrock failed to produce valid routing JSON after retry. "
        f"Last response: {json.dumps(retry_parsed) if retry_parsed is not None else '(unparseable)'}"
    )
