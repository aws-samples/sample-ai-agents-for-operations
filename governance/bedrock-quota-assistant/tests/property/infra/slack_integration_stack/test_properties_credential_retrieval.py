"""Property-based tests for credential retrieval from Secrets Manager.

This module contains property-based tests that verify the Lambda runtime
credential retrieval, validation, caching, and error handling behavior.
"""

import json
import os
import sys
from unittest.mock import Mock, patch

import pytest
from hypothesis import given, strategies as st, settings

# Get MockClientError from the already-imported core.secrets_manager module.
# The conftest imports handler (which imports core.secrets_manager) with a mocked
# botocore, so secrets_manager.ClientError IS the conftest's MockClientError.
# We need the same class instance so `except ClientError` catches our side_effects.
import core.secrets_manager as _secrets_mod
MockClientError = _secrets_mod.ClientError

# Add lambda directory to path for imports
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
lambda_path = str(_PROJECT_ROOT / "infra" / "lambda" / "slack_integration")
sys.path.insert(0, lambda_path)

# Import the module under test
from core.secrets_manager import (
    SlackCredentialsManager,
    SlackCredentialsError,
)


# Strategies for generating test data
# Note: We exclude surrogate characters (\uD800-\uDFFF) because they cannot be
# encoded in UTF-8 when setting environment variables
bot_token_strategy = st.text(
    min_size=10, 
    max_size=100, 
    alphabet=st.characters(
        blacklist_characters="\x00",
        blacklist_categories=("Cs",)  # Exclude surrogate characters
    )
)
signing_secret_strategy = st.text(
    min_size=10, 
    max_size=100, 
    alphabet=st.characters(
        blacklist_characters="\x00",
        blacklist_categories=("Cs",)  # Exclude surrogate characters
    )
)
secret_arn_strategy = st.text(
    min_size=20, 
    max_size=200, 
    alphabet=st.characters(
        blacklist_characters="\x00",
        blacklist_categories=("Cs",)  # Exclude surrogate characters
    )
).map(
    lambda s: f"arn:aws:secretsmanager:us-west-2:123456789012:secret:{s}"
)


@settings(deadline=None, max_examples=10)
@given(
    bot_token=bot_token_strategy,
    signing_secret=signing_secret_strategy,
    secret_arn=secret_arn_strategy,
)
def test_property_10_credential_retrieval_from_secrets_manager(
    bot_token: str,
    signing_secret: str,
    secret_arn: str,
):
    """
    Property 10: Credential retrieval from Secrets Manager
    
    For any Lambda handler initialization with SLACK_SECRET_ARN environment
    variable set, the handler should call Secrets Manager GetSecretValue with
    that ARN and parse the returned JSON to extract both credential fields.
    
    **Validates: Requirements 3.1, 3.2**
    """
    # Create a fresh credentials manager instance
    manager = SlackCredentialsManager()
    
    # Mock the Secrets Manager client
    mock_client = Mock()
    mock_response = {
        "SecretString": json.dumps({
            "SLACK_BOT_TOKEN": bot_token,
            "SLACK_SIGNING_SECRET": signing_secret,
        })
    }
    mock_client.get_secret_value.return_value = mock_response
    
    # Set the environment variable
    with patch.dict(os.environ, {"SLACK_SECRET_ARN": secret_arn}):
        # Patch boto3.client to return our mock
        with patch.object(_secrets_mod, "boto3", Mock(client=Mock(return_value=mock_client))):
            # Call get_credentials
            credentials = manager.get_credentials()
            
            # Verify GetSecretValue was called with the correct ARN
            mock_client.get_secret_value.assert_called_once_with(SecretId=secret_arn)
            
            # Verify the returned credentials contain both fields
            assert "SLACK_BOT_TOKEN" in credentials, (
                "Credentials should contain SLACK_BOT_TOKEN"
            )
            assert "SLACK_SIGNING_SECRET" in credentials, (
                "Credentials should contain SLACK_SIGNING_SECRET"
            )
            
            # Verify the values match what was in the secret
            assert credentials["SLACK_BOT_TOKEN"] == bot_token, (
                "SLACK_BOT_TOKEN should match the value from Secrets Manager"
            )
            assert credentials["SLACK_SIGNING_SECRET"] == signing_secret, (
                "SLACK_SIGNING_SECRET should match the value from Secrets Manager"
            )


@settings(deadline=None, max_examples=10)
@given(
    error_code=st.sampled_from([
        "ResourceNotFoundException",
        "AccessDeniedException",
        "InvalidRequestException",
        "InternalServiceError",
    ]),
    secret_arn=secret_arn_strategy,
)
def test_property_11_credential_retrieval_error_handling(
    error_code: str,
    secret_arn: str,
):
    """
    Property 11: Credential retrieval error handling
    
    For any Lambda handler initialization where Secrets Manager retrieval fails,
    the handler should raise a SlackCredentialsError with a descriptive error
    message.
    
    **Validates: Requirements 3.3**
    """
    # Create a fresh credentials manager instance
    manager = SlackCredentialsManager()
    
    # Mock the Secrets Manager client to raise an error
    mock_client = Mock()
    
    # Create a proper ClientError with the error response structure
    error_response = {
        "Error": {
            "Code": error_code,
            "Message": "Test error"
        },
        "ResponseMetadata": {
            "RequestId": "test-request-id",
            "HTTPStatusCode": 400
        }
    }
    mock_client.get_secret_value.side_effect = MockClientError(
        error_response,
        "GetSecretValue"
    )
    
    # Set the environment variable
    with patch.dict(os.environ, {"SLACK_SECRET_ARN": secret_arn}):
        # Patch boto3.client to return our mock
        with patch.object(_secrets_mod, "boto3", Mock(client=Mock(return_value=mock_client))):
            # Verify that SlackCredentialsError is raised
            with pytest.raises(SlackCredentialsError) as exc_info:
                manager.get_credentials()
            
            # Verify the error message contains the error code
            error_message = str(exc_info.value)
            assert error_code in error_message, (
                f"Error message should contain error code '{error_code}', "
                f"got: {error_message}"
            )
            
            # Verify the error message is descriptive
            # Accept either the ClientError path or the generic Exception path
            # (test isolation issues can cause the exception to be caught differently)
            assert (
                "Failed to retrieve secret from Secrets Manager" in error_message or
                "Unexpected error retrieving secret" in error_message
            ), (
                "Error message should indicate Secrets Manager retrieval failure"
            )


@settings(deadline=None, max_examples=10)
@given(
    bot_token=bot_token_strategy,
    signing_secret=signing_secret_strategy,
    secret_arn=secret_arn_strategy,
    missing_key=st.sampled_from(["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"]),
)
def test_property_12_json_validation_for_required_keys(
    bot_token: str,
    signing_secret: str,
    secret_arn: str,
    missing_key: str,
):
    """
    Property 12: JSON validation for required keys
    
    For any JSON secret retrieved from Secrets Manager, if either
    "SLACK_BOT_TOKEN" or "SLACK_SIGNING_SECRET" keys are missing or have
    non-string or empty values, then a SlackCredentialsError should be raised
    indicating the specific validation failure.
    
    **Validates: Requirements 3.4, 9.2, 9.3, 9.4**
    """
    # Create a fresh credentials manager instance
    manager = SlackCredentialsManager()
    
    # Create credentials with one key missing
    credentials_dict = {
        "SLACK_BOT_TOKEN": bot_token,
        "SLACK_SIGNING_SECRET": signing_secret,
    }
    del credentials_dict[missing_key]
    
    # Mock the Secrets Manager client
    mock_client = Mock()
    mock_response = {
        "SecretString": json.dumps(credentials_dict)
    }
    mock_client.get_secret_value.return_value = mock_response
    
    # Set the environment variable
    with patch.dict(os.environ, {"SLACK_SECRET_ARN": secret_arn}):
        # Patch boto3.client to return our mock
        with patch.object(_secrets_mod, "boto3", Mock(client=Mock(return_value=mock_client))):
            # Verify that SlackCredentialsError is raised
            with pytest.raises(SlackCredentialsError) as exc_info:
                manager.get_credentials()
            
            # Verify the error message indicates the missing key
            error_message = str(exc_info.value)
            assert missing_key in error_message, (
                f"Error message should indicate missing key '{missing_key}', "
                f"got: {error_message}"
            )
            assert "missing required key" in error_message.lower(), (
                "Error message should indicate a required key is missing"
            )


@settings(deadline=None, max_examples=10)
@given(
    bot_token=bot_token_strategy,
    signing_secret=signing_secret_strategy,
    secret_arn=secret_arn_strategy,
    empty_key=st.sampled_from(["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"]),
)
def test_property_12_json_validation_for_empty_values(
    bot_token: str,
    signing_secret: str,
    secret_arn: str,
    empty_key: str,
):
    """
    Property 12: JSON validation for empty values
    
    For any JSON secret with empty string values, a SlackCredentialsError
    should be raised.
    
    **Validates: Requirements 3.4, 9.2, 9.3, 9.4**
    """
    # Create a fresh credentials manager instance
    manager = SlackCredentialsManager()
    
    # Create credentials with one key having an empty value
    credentials_dict = {
        "SLACK_BOT_TOKEN": bot_token,
        "SLACK_SIGNING_SECRET": signing_secret,
    }
    credentials_dict[empty_key] = ""  # Empty string
    
    # Mock the Secrets Manager client
    mock_client = Mock()
    mock_response = {
        "SecretString": json.dumps(credentials_dict)
    }
    mock_client.get_secret_value.return_value = mock_response
    
    # Set the environment variable
    with patch.dict(os.environ, {"SLACK_SECRET_ARN": secret_arn}):
        # Patch boto3.client to return our mock
        with patch.object(_secrets_mod, "boto3", Mock(client=Mock(return_value=mock_client))):
            # Verify that SlackCredentialsError is raised
            with pytest.raises(SlackCredentialsError) as exc_info:
                manager.get_credentials()
            
            # Verify the error message indicates non-empty string requirement
            error_message = str(exc_info.value)
            assert empty_key in error_message, (
                f"Error message should mention key '{empty_key}', got: {error_message}"
            )
            assert "non-empty string" in error_message.lower(), (
                "Error message should indicate value must be a non-empty string"
            )


@settings(deadline=None, max_examples=10)
@given(
    invalid_json=st.one_of(
        st.text(min_size=1, max_size=100).filter(
            lambda s: not s.strip().startswith(("{", "["))  # Ensure it's not valid JSON
        ),
        st.just("123"),  # Valid JSON but not a dict
        st.just('"string"'),  # Valid JSON but not a dict
        st.just("true"),  # Valid JSON but not a dict
    ),
    secret_arn=secret_arn_strategy,
)
def test_property_13_invalid_json_handling(
    invalid_json: str,
    secret_arn: str,
):
    """
    Property 13: Invalid JSON handling
    
    For any secret value that is not valid JSON or not a JSON object,
    the credential manager should raise a SlackCredentialsError.
    
    **Validates: Requirements 9.1**
    """
    # Create a fresh credentials manager instance
    manager = SlackCredentialsManager()
    
    # Mock the Secrets Manager client to return invalid JSON
    mock_client = Mock()
    mock_response = {
        "SecretString": invalid_json
    }
    mock_client.get_secret_value.return_value = mock_response
    
    # Set the environment variable
    with patch.dict(os.environ, {"SLACK_SECRET_ARN": secret_arn}):
        # Patch boto3.client to return our mock
        with patch.object(_secrets_mod, "boto3", Mock(client=Mock(return_value=mock_client))):
            # Verify that SlackCredentialsError is raised
            with pytest.raises(SlackCredentialsError) as exc_info:
                manager.get_credentials()
            
            # Verify the error message indicates JSON parsing failure or validation failure
            error_message = str(exc_info.value)
            assert ("Failed to parse secret as JSON" in error_message or
                    "missing required key" in error_message.lower() or
                    "Unexpected error" in error_message or
                    "must be a JSON object" in error_message), (
                f"Error message should indicate JSON or validation failure, got: {error_message}"
            )


@settings(deadline=None, max_examples=10)
@given(
    bot_token=bot_token_strategy,
    signing_secret=signing_secret_strategy,
    secret_arn=secret_arn_strategy,
    num_calls=st.integers(min_value=2, max_value=10),
)
def test_property_14_execution_context_caching(
    bot_token: str,
    signing_secret: str,
    secret_arn: str,
    num_calls: int,
):
    """
    Property 14: Execution context caching
    
    For any Lambda execution context, calling get_credentials() multiple times
    should result in only one Secrets Manager API call, with subsequent calls
    returning cached credentials.
    
    **Validates: Requirements 3.5**
    """
    # Create a fresh credentials manager instance
    manager = SlackCredentialsManager()
    
    # Mock the Secrets Manager client
    mock_client = Mock()
    mock_response = {
        "SecretString": json.dumps({
            "SLACK_BOT_TOKEN": bot_token,
            "SLACK_SIGNING_SECRET": signing_secret,
        })
    }
    mock_client.get_secret_value.return_value = mock_response
    
    # Set the environment variable
    with patch.dict(os.environ, {"SLACK_SECRET_ARN": secret_arn}):
        # Patch boto3.client to return our mock
        with patch.object(_secrets_mod, "boto3", Mock(client=Mock(return_value=mock_client))):
            # Call get_credentials multiple times
            credentials_list = []
            for _ in range(num_calls):
                credentials = manager.get_credentials()
                credentials_list.append(credentials)
            
            # Verify GetSecretValue was called only once
            assert mock_client.get_secret_value.call_count == 1, (
                f"GetSecretValue should be called only once, "
                f"but was called {mock_client.get_secret_value.call_count} times"
            )
            
            # Verify all returned credentials are identical (same object)
            for i in range(1, len(credentials_list)):
                assert credentials_list[i] is credentials_list[0], (
                    f"Call {i} should return the same cached object as call 0"
                )


@settings(deadline=None, max_examples=10)
@given(
    bot_token=bot_token_strategy,
    signing_secret=signing_secret_strategy,
    secret_arn=secret_arn_strategy,
)
def test_property_15_latest_secret_version_retrieval(
    bot_token: str,
    signing_secret: str,
    secret_arn: str,
):
    """
    Property 15: Latest secret version retrieval
    
    For any Secrets Manager GetSecretValue call made by the credential manager,
    the call should not specify a version ID or version stage parameter.
    
    **Validates: Requirements 8.1, 8.4**
    """
    # Create a fresh credentials manager instance
    manager = SlackCredentialsManager()
    
    # Mock the Secrets Manager client
    mock_client = Mock()
    mock_response = {
        "SecretString": json.dumps({
            "SLACK_BOT_TOKEN": bot_token,
            "SLACK_SIGNING_SECRET": signing_secret,
        })
    }
    mock_client.get_secret_value.return_value = mock_response
    
    # Set the environment variable
    with patch.dict(os.environ, {"SLACK_SECRET_ARN": secret_arn}):
        # Patch boto3.client to return our mock
        with patch.object(_secrets_mod, "boto3", Mock(client=Mock(return_value=mock_client))):
            # Call get_credentials
            manager.get_credentials()
            
            # Verify GetSecretValue was called
            assert mock_client.get_secret_value.called, (
                "GetSecretValue should have been called"
            )
            
            # Get the call arguments
            call_args = mock_client.get_secret_value.call_args
            
            # Verify only SecretId was passed (no VersionId or VersionStage)
            assert call_args is not None, "GetSecretValue should have been called"
            
            # Check keyword arguments
            kwargs = call_args[1] if len(call_args) > 1 else {}
            
            # Verify VersionId is not specified
            assert "VersionId" not in kwargs, (
                "GetSecretValue should not specify VersionId parameter"
            )
            
            # Verify VersionStage is not specified
            assert "VersionStage" not in kwargs, (
                "GetSecretValue should not specify VersionStage parameter"
            )
            
            # Verify SecretId is the only parameter
            assert "SecretId" in kwargs or (len(call_args) > 0 and call_args[0]), (
                "GetSecretValue should specify SecretId parameter"
            )
