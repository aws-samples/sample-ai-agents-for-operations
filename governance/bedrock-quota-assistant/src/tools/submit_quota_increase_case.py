# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Tool: submit_quota_increase_case — submits a previously drafted quota increase as an AWS Support case."""

import json as _json
import logging

import boto3
from botocore.exceptions import ClientError
from strands import tool


logger = logging.getLogger(__name__)


@tool
def submit_quota_increase_case(confirm: str = "no", subject: str = None, case_body: str = None, severity: str = "normal") -> str:
    """Submit the previously drafted quota increase request as an AWS Support case.

    IMPORTANT: This tool creates a REAL support case. Only call it after:
    1. draft_quota_increase_request has been called and the draft is ready
    2. The user has EXPLICITLY confirmed they want you to submit it

    The confirm parameter MUST be "yes" to proceed. If the user has not clearly
    said something like "yes, submit it", "go ahead", "please raise it", etc.,
    do NOT call this tool. Instead ask for confirmation.

    If calling in the same turn as draft_quota_increase_request, subject and
    case_body are optional (read from the draft automatically). If calling in a
    follow-up turn, provide the subject and case_body from the earlier draft.

    Args:
        confirm: Must be "yes" to proceed with submission. Any other value aborts.
        subject: Case subject line. Optional if draft was generated in the same turn.
        case_body: Full case body text. Optional if draft was generated in the same turn.
        severity: Case severity. One of: "low", "normal", "high", "urgent", "critical". Default: "normal".

    Returns:
        JSON with case ID on success, or error message on failure.
    """
    # Import the mutable module-level reference
    import tools.draft_quota_increase_request as _draft_module
    _draft_data = _draft_module._last_draft_data

    # Validate severity
    valid_severities = ("low", "normal", "high", "urgent", "critical")
    _severity = severity.lower() if severity else "normal"
    if _severity not in valid_severities:
        return _json.dumps({
            "status": "error",
            "message": f"Invalid severity '{severity}'. Must be one of: {', '.join(valid_severities)}"
        })

    if confirm.lower() != "yes":
        return _json.dumps({
            "status": "aborted",
            "message": "Submission cancelled. The user must explicitly confirm before submitting. "
                       "Ask the user: 'Would you like me to submit this case for you?'"
        })

    # Resolve subject and case_body: prefer explicit params, fall back to draft
    _subject = subject
    _body = case_body

    if _draft_data is not None:
        if not _subject:
            _subject = _draft_data.get("subject", "")
        if not _body:
            _body = _draft_data.get("case_body", "")

    if not _subject or not _body:
        return _json.dumps({
            "status": "error",
            "message": "No draft available. Call draft_quota_increase_request first to prepare the case, "
                       "or provide subject and case_body parameters."
        })

    try:
        # AWS Support API must be called in us-east-1
        support_client = boto3.client("support", region_name="us-east-1")

        response = support_client.create_case(
            subject=_subject,
            serviceCode="service-service-quotas",
            severityCode=_severity,
            categoryCode="general",
            communicationBody=_body,
            language="en",
            issueType="customer-service",
        )

        case_id = response.get("caseId", "unknown")

        # Get the user-facing display ID by describing the case
        display_id = case_id  # fallback
        try:
            desc = support_client.describe_cases(caseIdList=[case_id], includeResolvedCases=False)
            cases = desc.get("cases", [])
            if cases:
                display_id = cases[0].get("displayId", case_id)
        except Exception as e:
            logger.warning(f"Could not fetch displayId for case {case_id}: {e}")

        # Mark that the case was submitted so _append_draft_actions knows
        if _draft_data is not None:
            _draft_data["submitted"] = True
            _draft_data["case_id"] = display_id

        return _json.dumps({
            "status": "submitted",
            "case_id": display_id,
            "internal_case_id": case_id,
            "message": f"Support case created successfully. Case ID: {display_id}. "
                       f"You can track it at https://console.aws.amazon.com/support/home#/case/?displayId={display_id}"
        })

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]

        if error_code == "SubscriptionRequiredException":
            return _json.dumps({
                "status": "error",
                "error_code": error_code,
                "message": "This account doesn't have a Business or Enterprise Support plan, "
                           "which is required to create cases via the API. "
                           "The user can submit manually via the console or CLI instead."
            })
        else:
            return _json.dumps({
                "status": "error",
                "error_code": error_code,
                "message": f"Failed to create support case: {error_msg}"
            })
    except Exception as e:
        return _json.dumps({
            "status": "error",
            "message": f"Unexpected error creating support case: {str(e)}"
        })
