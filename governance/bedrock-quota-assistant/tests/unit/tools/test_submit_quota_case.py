# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for submit_quota_increase_case tool."""

import importlib
import json
from unittest.mock import patch, MagicMock

import pytest
from botocore.exceptions import ClientError

_mod = importlib.import_module("tools.submit_quota_increase_case")
submit_quota_increase_case = _mod.submit_quota_increase_case


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

CASE_SUBJECT = "Bedrock quota increase: Claude Sonnet 4 in us-west-2"
CASE_BODY = (
    "Requesting a quota increase for Amazon Bedrock model: Claude Sonnet 4\n"
    "Model ID: anthropic.claude-sonnet-4-20250514-v1:0\n"
    "Region: us-west-2\n\n"
    "== Use Case ==\nAI assistant for 500 engineers"
)

DRAFT_DATA = {
    "subject": CASE_SUBJECT,
    "case_body": CASE_BODY,
    "cli_command_file": "aws support create-case ...",
    "cli_command_inline": "aws support create-case ...",
    "links": {
        "support_console": "https://console.aws.amazon.com/support/home#/case/create",
        "service_quotas": "https://console.aws.amazon.com/servicequotas/home?region=us-west-2#!/services/bedrock/quotas",
    },
    "note": "Requires Business or Enterprise Support plan.",
}


@pytest.fixture(autouse=True)
def reset_draft_module():
    """Reset the _last_draft_data between tests."""
    import tools.draft_quota_increase_request as draft_mod
    draft_mod._last_draft_data = None
    yield
    draft_mod._last_draft_data = None


# ---------------------------------------------------------------------------
# Test: confirm != "yes" → aborted
# ---------------------------------------------------------------------------


class TestConfirmAborted:
    """Submission must abort when confirm is not 'yes'."""

    def test_confirm_no_returns_aborted(self):
        result = submit_quota_increase_case(confirm="no")
        data = json.loads(result)
        assert data["status"] == "aborted"
        assert "cancelled" in data["message"].lower()

    def test_confirm_empty_string_returns_aborted(self):
        result = submit_quota_increase_case(confirm="")
        data = json.loads(result)
        assert data["status"] == "aborted"

    def test_confirm_maybe_returns_aborted(self):
        result = submit_quota_increase_case(confirm="maybe")
        data = json.loads(result)
        assert data["status"] == "aborted"

    def test_confirm_yes_uppercase_proceeds(self):
        """'YES' should also be accepted (case-insensitive)."""
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        mock_support = MagicMock()
        mock_support.create_case.return_value = {"caseId": "case-123"}
        mock_support.describe_cases.return_value = {"cases": [{"displayId": "1234567890"}]}

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            result = submit_quota_increase_case(confirm="YES")

        data = json.loads(result)
        assert data["status"] == "submitted"


# ---------------------------------------------------------------------------
# Test: Invalid severity → error
# ---------------------------------------------------------------------------


class TestInvalidSeverity:
    """Invalid severity values should return an error."""

    def test_invalid_severity_returns_error(self):
        result = submit_quota_increase_case(confirm="yes", severity="extreme")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Invalid severity" in data["message"]
        assert "extreme" in data["message"]

    def test_valid_severities_accepted(self):
        """All valid severities should pass validation."""
        import tools.draft_quota_increase_request as draft_mod

        for sev in ("low", "normal", "high", "urgent", "critical"):
            draft_mod._last_draft_data = DRAFT_DATA.copy()

            mock_support = MagicMock()
            mock_support.create_case.return_value = {"caseId": "case-abc"}
            mock_support.describe_cases.return_value = {"cases": [{"displayId": "9999"}]}

            with patch.object(_mod, "boto3") as mock_boto3:
                mock_boto3.client.return_value = mock_support
                result = submit_quota_increase_case(confirm="yes", severity=sev)

            data = json.loads(result)
            assert data["status"] == "submitted", f"Severity '{sev}' should be valid"


# ---------------------------------------------------------------------------
# Test: No draft and no params → error message
# ---------------------------------------------------------------------------


class TestNoDraftNoParams:
    """When no draft data exists and no subject/case_body provided."""

    def test_no_draft_no_params_returns_error(self):
        result = submit_quota_increase_case(confirm="yes")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "No draft available" in data["message"]
        assert "draft_quota_increase_request" in data["message"]

    def test_no_draft_only_subject_returns_error(self):
        result = submit_quota_increase_case(confirm="yes", subject="Some subject")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "No draft available" in data["message"]

    def test_no_draft_only_body_returns_error(self):
        result = submit_quota_increase_case(confirm="yes", case_body="Some body")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "No draft available" in data["message"]


# ---------------------------------------------------------------------------
# Test: Successful submission
# ---------------------------------------------------------------------------


class TestSuccessfulSubmission:
    """Mock create_case and describe_cases for a successful submission."""

    def test_successful_submission_returns_case_id(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        mock_support = MagicMock()
        mock_support.create_case.return_value = {"caseId": "case-internal-id-abc"}
        mock_support.describe_cases.return_value = {
            "cases": [{"displayId": "5678901234"}]
        }

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            result = submit_quota_increase_case(confirm="yes")

        data = json.loads(result)
        assert data["status"] == "submitted"
        assert data["case_id"] == "5678901234"
        assert data["internal_case_id"] == "case-internal-id-abc"
        assert "5678901234" in data["message"]
        assert "console.aws.amazon.com/support" in data["message"]

    def test_create_case_called_with_correct_params(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        mock_support = MagicMock()
        mock_support.create_case.return_value = {"caseId": "case-xyz"}
        mock_support.describe_cases.return_value = {"cases": []}

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            submit_quota_increase_case(confirm="yes", severity="high")

        mock_boto3.client.assert_called_once_with("support", region_name="us-east-1")
        mock_support.create_case.assert_called_once_with(
            subject=CASE_SUBJECT,
            serviceCode="service-service-quotas",
            severityCode="high",
            categoryCode="general",
            communicationBody=CASE_BODY,
            language="en",
            issueType="customer-service",
        )

    def test_describe_cases_failure_uses_internal_id_as_fallback(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        mock_support = MagicMock()
        mock_support.create_case.return_value = {"caseId": "case-fallback-id"}
        mock_support.describe_cases.side_effect = Exception("Describe failed")

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            result = submit_quota_increase_case(confirm="yes")

        data = json.loads(result)
        assert data["status"] == "submitted"
        # Falls back to internal case ID when describe_cases fails
        assert data["case_id"] == "case-fallback-id"

    def test_explicit_subject_and_body_override_draft(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        mock_support = MagicMock()
        mock_support.create_case.return_value = {"caseId": "case-override"}
        mock_support.describe_cases.return_value = {"cases": [{"displayId": "OVERRIDE-123"}]}

        custom_subject = "Custom subject line"
        custom_body = "Custom body text"

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            submit_quota_increase_case(
                confirm="yes",
                subject=custom_subject,
                case_body=custom_body,
            )

        mock_support.create_case.assert_called_once()
        call_kwargs = mock_support.create_case.call_args[1]
        assert call_kwargs["subject"] == custom_subject
        assert call_kwargs["communicationBody"] == custom_body

    def test_marks_draft_as_submitted(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        mock_support = MagicMock()
        mock_support.create_case.return_value = {"caseId": "case-mark"}
        mock_support.describe_cases.return_value = {"cases": [{"displayId": "MARK-123"}]}

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            submit_quota_increase_case(confirm="yes")

        assert draft_mod._last_draft_data["submitted"] is True
        assert draft_mod._last_draft_data["case_id"] == "MARK-123"


# ---------------------------------------------------------------------------
# Test: SubscriptionRequiredException
# ---------------------------------------------------------------------------


class TestSubscriptionRequired:
    """Account without Business/Enterprise support plan."""

    def test_subscription_required_returns_appropriate_error(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        error_response = {
            "Error": {"Code": "SubscriptionRequiredException", "Message": "Subscription required"}
        }
        client_error = ClientError(error_response, "CreateCase")

        mock_support = MagicMock()
        mock_support.create_case.side_effect = client_error

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            result = submit_quota_increase_case(confirm="yes")

        data = json.loads(result)
        assert data["status"] == "error"
        assert data["error_code"] == "SubscriptionRequiredException"
        assert "Business or Enterprise Support plan" in data["message"]
        assert "console or CLI" in data["message"]

    def test_other_client_error_returns_error_message(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        error_response = {
            "Error": {"Code": "AccessDeniedException", "Message": "User not authorized"}
        }
        client_error = ClientError(error_response, "CreateCase")

        mock_support = MagicMock()
        mock_support.create_case.side_effect = client_error

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            result = submit_quota_increase_case(confirm="yes")

        data = json.loads(result)
        assert data["status"] == "error"
        assert data["error_code"] == "AccessDeniedException"
        assert "User not authorized" in data["message"]

    def test_unexpected_exception_returns_error(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        mock_support = MagicMock()
        mock_support.create_case.side_effect = RuntimeError("Network timeout")

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            result = submit_quota_increase_case(confirm="yes")

        data = json.loads(result)
        assert data["status"] == "error"
        assert "Unexpected error" in data["message"]
        assert "Network timeout" in data["message"]


# ---------------------------------------------------------------------------
# Test: Uses _last_draft_data from draft module
# ---------------------------------------------------------------------------


class TestUsesDraftData:
    """When subject/case_body not provided, reads from draft module state."""

    def test_reads_subject_from_draft_when_not_provided(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = DRAFT_DATA.copy()

        mock_support = MagicMock()
        mock_support.create_case.return_value = {"caseId": "case-from-draft"}
        mock_support.describe_cases.return_value = {"cases": [{"displayId": "DRAFT-001"}]}

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_support
            submit_quota_increase_case(confirm="yes")

        call_kwargs = mock_support.create_case.call_args[1]
        assert call_kwargs["subject"] == CASE_SUBJECT
        assert call_kwargs["communicationBody"] == CASE_BODY

    def test_draft_with_empty_subject_and_no_explicit_subject_errors(self):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = {"subject": "", "case_body": CASE_BODY}

        result = submit_quota_increase_case(confirm="yes")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "No draft available" in data["message"]
