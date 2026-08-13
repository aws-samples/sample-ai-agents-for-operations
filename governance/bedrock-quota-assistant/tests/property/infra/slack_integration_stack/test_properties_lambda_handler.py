"""
Property-based tests for Lambda handler credential security.

These tests verify that the Lambda handler does not log credentials in any form,
ensuring security requirements are met across all valid inputs.
"""

import json
from pathlib import Path
import pytest
from hypothesis import given, strategies as st, settings
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Set required environment variables before importing handler
os.environ['AGENTCORE_ARN'] = 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test'
os.environ['AGENTCORE_REGION'] = 'us-west-2'
os.environ['ENVIRONMENT'] = 'test'
os.environ['AWS_LAMBDA_FUNCTION_NAME'] = 'test-function'
# Required: the handler refuses to start without it, because event deduplication
# bounds how many Amazon Bedrock invocations a retried Slack webhook can trigger.
os.environ['DEDUP_TABLE_NAME'] = 'test-dedup-table'
os.environ['SLACK_SECRET_ARN'] = 'arn:aws:secretsmanager:us-west-2:123456789012:secret:test-secret'

# Mocks are set up by conftest.py (handler is already imported with mocked deps).
# Create a local mock_boto3 for tests that need to override client behavior.
mock_boto3 = Mock()
mock_boto3.client = Mock(side_effect=lambda *a, **kw: MagicMock())

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


# Strategy for generating realistic Slack bot tokens
slack_bot_token_strategy = st.text(
    alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-',
    min_size=50,
    max_size=100
).map(lambda s: f"xoxb-{s}")

# Strategy for generating realistic Slack signing secrets
slack_signing_secret_strategy = st.text(
    alphabet='abcdef0123456789',
    min_size=32,
    max_size=32
)


@given(
    bot_token=slack_bot_token_strategy,
    signing_secret=slack_signing_secret_strategy,
)
@settings(max_examples=50)
@pytest.mark.property_test
def test_property_17_no_credential_logging(bot_token, signing_secret):
    """
    Property 17: No credential logging
    
    For any Lambda handler execution with any set of credentials, no log output should
    contain any substring of SLACK_BOT_TOKEN or SLACK_SIGNING_SECRET values, including
    truncated or preview versions.
    
    Feature: slack-secrets-manager-migration, Property 17: No credential logging
    
    Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5
    """
    # Mock the secrets manager to return our test credentials
    mock_secrets_client = MagicMock()
    mock_secrets_client.get_secret_value.return_value = {
        'SecretString': json.dumps({
            'SLACK_BOT_TOKEN': bot_token,
            'SLACK_SIGNING_SECRET': signing_secret
        })
    }
    mock_boto3.client.return_value = mock_secrets_client
    
    # Capture all log calls
    captured_logs = []
    
    def capture_log(*args, **kwargs):
        # Capture the message
        if args:
            captured_logs.append(str(args[0]))
        # Capture extra fields
        extra = kwargs.get('extra', {})
        for key, value in extra.items():
            captured_logs.append(str(value))
    
    # Import handler after mocking (this will trigger credential retrieval)
    # We need to reload the module to test initialization
    if 'handler' in sys.modules:
        del sys.modules['handler']
    
    # Mock the logger before importing
    with patch('core.utils.setup_structured_logging') as mock_logger_setup:
        mock_logger = MagicMock()
        mock_logger.info.side_effect = capture_log
        mock_logger.warning.side_effect = capture_log
        mock_logger.error.side_effect = capture_log
        mock_logger.debug.side_effect = capture_log
        mock_logger_setup.return_value = mock_logger
        
        # Import handler (this triggers credential retrieval and initialization)
        import handler
        
        # Also test during a Lambda invocation
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request-123'
        
        event = {
            "async_process": True,
            "prompt": "test prompt",
            "channel": "C123456",
            "thread_ts": "1234567890.123456",
            "user_id": "U123456"
        }
        
        # Mock dependencies
        with patch.object(handler.agentcore_client, 'invoke', return_value="response"):
            with patch.object(handler.slack_client, 'post_message'):
                handler.lambda_handler(event, mock_context)
    
    # Verify no credential values appear in logs
    all_logs = ' '.join(captured_logs)
    
    # Check that the full bot token doesn't appear
    assert bot_token not in all_logs, \
        "SLACK_BOT_TOKEN value must not appear in logs"
    
    # Check that the full signing secret doesn't appear
    assert signing_secret not in all_logs, \
        "SLACK_SIGNING_SECRET value must not appear in logs"
    
    # Check for common truncation patterns (first 5 chars, last 4 chars, etc.)
    if len(bot_token) > 9:
        token_preview_patterns = [
            bot_token[:5],  # First 5 chars
            bot_token[-4:],  # Last 4 chars
            bot_token[:10],  # First 10 chars
            bot_token[-10:],  # Last 10 chars
        ]
        for pattern in token_preview_patterns:
            if len(pattern) >= 4:  # Only check meaningful patterns
                assert pattern not in all_logs, \
                    f"Truncated SLACK_BOT_TOKEN value '{pattern}' must not appear in logs"
    
    if len(signing_secret) > 9:
        secret_preview_patterns = [
            signing_secret[:5],  # First 5 chars
            signing_secret[-4:],  # Last 4 chars
            signing_secret[:10],  # First 10 chars
            signing_secret[-10:],  # Last 10 chars
        ]
        for pattern in secret_preview_patterns:
            if len(pattern) >= 4:  # Only check meaningful patterns
                assert pattern not in all_logs, \
                    f"Truncated SLACK_SIGNING_SECRET value '{pattern}' must not appear in logs"
    
    # Verify that success message is logged (without credentials)
    success_logged = any('successfully' in log.lower() and 'credentials' in log.lower() 
                        for log in captured_logs)
    assert success_logged, \
        "Success message should be logged when credentials are retrieved"



@given(
    bot_token=slack_bot_token_strategy,
    signing_secret=slack_signing_secret_strategy,
)
@settings(max_examples=50)
@pytest.mark.property_test
def test_property_16_validation_before_slack_app_initialization(bot_token, signing_secret):
    """
    Property 16: Validation before Slack App initialization
    
    For any Lambda handler initialization, credential validation should complete
    successfully before the Slack Bolt App is instantiated.
    
    Feature: slack-secrets-manager-migration, Property 16: Validation before Slack App initialization
    
    Validates: Requirements 9.5
    """
    # This test verifies the initialization order by checking the handler code structure
    # We verify that:
    # 1. get_slack_credentials() is called before App() initialization
    # 2. Credentials are extracted before App() initialization
    # 3. Any SlackCredentialsError is caught before App() initialization
    
    # Read the handler.py file to verify the code structure
    handler_path = str(_PROJECT_ROOT / "infra" / "lambda" / "slack_integration" / "handler.py")
    
    with open(handler_path, 'r') as f:
        handler_code = f.read()
    
    # Find the positions of key operations in the code
    credentials_call_pos = handler_code.find('get_slack_credentials()')
    app_init_pos = handler_code.find('app = App(')
    
    # Verify both operations exist
    assert credentials_call_pos != -1, \
        "Handler must call get_slack_credentials()"
    assert app_init_pos != -1, \
        "Handler must initialize Slack App"
    
    # Verify credentials are retrieved before App initialization
    assert credentials_call_pos < app_init_pos, \
        "Credential retrieval must happen before Slack App initialization in the code"
    
    # Verify that credential extraction happens before App init
    bot_token_extract_pos = handler_code.find('slack_bot_token = credentials["SLACK_BOT_TOKEN"]')
    signing_secret_extract_pos = handler_code.find('slack_signing_secret = credentials["SLACK_SIGNING_SECRET"]')
    
    assert bot_token_extract_pos != -1, \
        "Handler must extract SLACK_BOT_TOKEN from credentials"
    assert signing_secret_extract_pos != -1, \
        "Handler must extract SLACK_SIGNING_SECRET from credentials"
    
    assert bot_token_extract_pos < app_init_pos, \
        "SLACK_BOT_TOKEN extraction must happen before App initialization"
    assert signing_secret_extract_pos < app_init_pos, \
        "SLACK_SIGNING_SECRET extraction must happen before App initialization"
    
    # Verify that error handling wraps credential retrieval
    try_pos = handler_code.find('try:', credentials_call_pos - 100, credentials_call_pos)
    except_pos = handler_code.find('except SlackCredentialsError', credentials_call_pos)
    
    assert try_pos != -1 and try_pos < credentials_call_pos, \
        "Credential retrieval must be wrapped in try-except"
    assert except_pos != -1 and except_pos < app_init_pos, \
        "SlackCredentialsError handling must occur before App initialization"
    
    # Verify that the except block raises the error (preventing App initialization)
    except_block_start = except_pos
    except_block_end = handler_code.find('\n\n', except_block_start)
    except_block = handler_code[except_block_start:except_block_end]
    
    assert 'raise' in except_block, \
        "SlackCredentialsError must be re-raised to prevent App initialization"


@given(
    error_message=st.text(min_size=10, max_size=100)
)
@settings(max_examples=10)
@pytest.mark.property_test
def test_property_16_validation_failure_prevents_initialization(error_message):
    """
    Property 16: Validation failure prevents initialization (error case)
    
    For any Lambda handler initialization where credential validation fails,
    the Slack Bolt App should not be instantiated and an error should be raised.
    
    This is verified by checking the code structure to ensure that credential
    retrieval errors are caught and re-raised before App initialization.
    
    Feature: slack-secrets-manager-migration, Property 16: Validation before Slack App initialization
    
    Validates: Requirements 9.5
    """
    # Read the handler.py file to verify error handling structure
    handler_path = str(_PROJECT_ROOT / "infra" / "lambda" / "slack_integration" / "handler.py")
    
    with open(handler_path, 'r') as f:
        handler_code = f.read()
    
    # Verify that credential retrieval is wrapped in try-except
    credentials_call_pos = handler_code.find('get_slack_credentials()')
    app_init_pos = handler_code.find('app = App(')
    
    # Find the try-except block
    try_pos = handler_code.find('try:', credentials_call_pos - 100, credentials_call_pos)
    except_pos = handler_code.find('except SlackCredentialsError', credentials_call_pos)
    
    assert try_pos != -1, "Credential retrieval must be in a try block"
    assert except_pos != -1, "Must have except SlackCredentialsError handler"
    assert try_pos < credentials_call_pos < except_pos < app_init_pos, \
        "Error handling must be between credential retrieval and App initialization"
    
    # Verify that the except block raises the error
    except_block_start = except_pos
    except_block_end = handler_code.find('\n\n', except_block_start)
    if except_block_end == -1:
        except_block_end = handler_code.find('\n# ', except_block_start)
    except_block = handler_code[except_block_start:except_block_end]
    
    assert 'raise' in except_block, \
        "SlackCredentialsError must be re-raised to prevent App initialization on error"

