"""Layer 1: Input validation — ensures clean, safe data enters the assessment pipeline.

Validates all inputs before they reach any agent or scoring logic.
Prevents garbage-in-garbage-out scenarios and prompt injection attacks.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from mio_agent.models.assessment import AccessTier, AssessmentRequest
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Maximum sizes to prevent oversized payloads
MAX_IaC_TEMPLATE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB
MAX_ACCOUNT_NAME_LENGTH = 256
MAX_TRIGGER_CONTEXT_KEYS = 20

# Prompt injection patterns — prevent adversarial inputs from hijacking the agent
_PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"disregard\s+all\s+prior",
    r"you\s+are\s+now\s+a",
    r"act\s+as\s+if\s+you\s+are",
    r"forget\s+everything",
    r"system\s+prompt",
    r"jailbreak",
    r"<\|.*?\|>",  # Special token injection
]


class InputValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.field = field


def validate_assessment_request(request_data: dict[str, Any]) -> AssessmentRequest:
    """Validate and parse an assessment request dict.

    Args:
        request_data: Raw request data dict.

    Returns:
        Validated AssessmentRequest.

    Raises:
        InputValidationError: If validation fails.
    """
    # Check for prompt injection in string fields
    for field_name in ("account_name", "requested_by"):
        value = request_data.get(field_name, "")
        if isinstance(value, str):
            _check_prompt_injection(value, field_name)

    # Check trigger_context size
    trigger_context = request_data.get("trigger_context", {})
    if isinstance(trigger_context, dict) and len(trigger_context) > MAX_TRIGGER_CONTEXT_KEYS:
        raise InputValidationError(
            f"trigger_context has too many keys ({len(trigger_context)}, max {MAX_TRIGGER_CONTEXT_KEYS})",
            field="trigger_context",
        )

    # Validate via Pydantic
    try:
        return AssessmentRequest(**request_data)
    except ValidationError as e:
        raise InputValidationError(f"Assessment request validation failed: {e}") from e


def validate_iac_template(template_content: str, template_format: str = "auto") -> str:
    """Validate an IaC template before processing.

    Args:
        template_content: Raw template string.
        template_format: Template format hint.

    Returns:
        Validated template content.

    Raises:
        InputValidationError: If template is invalid or too large.
    """
    if not template_content or not template_content.strip():
        raise InputValidationError("IaC template content is empty", field="template_content")

    # Size check
    size_bytes = len(template_content.encode("utf-8"))
    if size_bytes > MAX_IaC_TEMPLATE_SIZE_BYTES:
        raise InputValidationError(
            f"IaC template too large ({size_bytes:,} bytes, max {MAX_IaC_TEMPLATE_SIZE_BYTES:,})",
            field="template_content",
        )

    # Check for prompt injection in template content
    _check_prompt_injection(template_content, "template_content")

    return template_content


def validate_account_id(account_id: str) -> str:
    """Validate an AWS account ID.

    Args:
        account_id: Account ID string to validate.

    Returns:
        Validated account ID.

    Raises:
        InputValidationError: If account ID is invalid.
    """
    if not re.match(r"^\d{12}$", account_id):
        raise InputValidationError(
            f"Invalid AWS account ID format (must be 12 digits): {account_id!r}",
            field="account_id",
        )
    return account_id


def validate_role_arn(role_arn: str) -> str:
    """Validate an IAM role ARN.

    Args:
        role_arn: ARN string to validate.

    Returns:
        Validated ARN.

    Raises:
        InputValidationError: If ARN format is invalid.
    """
    pattern = r"^arn:aws:iam::\d{12}:role/[\w+=,.@/-]+$"
    if not re.match(pattern, role_arn):
        raise InputValidationError(
            f"Invalid IAM role ARN format: {role_arn!r}",
            field="role_arn",
        )
    return role_arn


def sanitize_narrative_input(findings_text: str) -> str:
    """Sanitize findings text before passing to Narrative Agent.

    Removes any content that could constitute a prompt injection attack
    if findings data from a customer environment contained adversarial content.

    Args:
        findings_text: Findings text to sanitize.

    Returns:
        Sanitized text.
    """
    sanitized = findings_text

    # Remove potential prompt injection patterns
    for pattern in _PROMPT_INJECTION_PATTERNS:
        sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE)

    if sanitized != findings_text:
        logger.warning("Sanitized potential prompt injection content from findings input")

    return sanitized


def _check_prompt_injection(value: str, field_name: str) -> None:
    """Check a string value for prompt injection patterns.

    Args:
        value: String to check.
        field_name: Field name for error reporting.

    Raises:
        InputValidationError: If injection pattern detected.
    """
    for pattern in _PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, value, re.IGNORECASE):
            logger.warning(
                "Potential prompt injection detected",
                extra={"field": field_name, "pattern": pattern},
            )
            raise InputValidationError(
                f"Invalid content detected in field '{field_name}': contains disallowed patterns",
                field=field_name,
            )
