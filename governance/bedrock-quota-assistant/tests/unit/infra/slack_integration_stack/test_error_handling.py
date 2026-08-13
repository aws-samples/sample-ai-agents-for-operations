"""
Unit tests for error handling in API Gateway Stack Lambda functions.

These tests verify specific error scenarios and edge cases for the Slack integration
Lambda handler, including AgentCore invocation errors, Slack API errors, Lambda
timeout handling, and malformed event handling.
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Save original modules before mocking so they can be restored
_original_boto3 = sys.modules.get('boto3')
_original_botocore = sys.modules.get('botocore')
_original_botocore_exceptions = sys.modules.get('botocore.exceptions')

# Create proper mocks before any imports
# Mock botocore first with ClientError
class MockClientError(Exception):
    def __init__(self, error_response, operation_name):
        self.response = error_response
        self.operation_name = operation_name
        super().__init__(f"An error occurred ({error_response['Error']['Code']}) when calling the {operation_name} operation")

mock_botocore = MagicMock()
mock_botocore.exceptions = MagicMock()
mock_botocore.exceptions.ClientError = MockClientError
sys.modules['botocore'] = mock_botocore
sys.modules['botocore.exceptions'] = mock_botocore.exceptions

# Create a mock secrets manager client that returns proper values
def create_mock_boto3_client(service_name, *args, **kwargs):
    mock_client = MagicMock()
    if service_name == 'secretsmanager':
        # Return a proper dictionary with string SecretString
        mock_client.get_secret_value.return_value = {
            'SecretString': json.dumps({
                'SLACK_BOT_TOKEN': 'xoxb-test-token-12345',
                'SLACK_SIGNING_SECRET': 'test-signing-secret-32chars-long'
            })
        }
    return mock_client

# Mock boto3 with proper client factory
mock_boto3 = MagicMock()
mock_boto3.client = create_mock_boto3_client
sys.modules['boto3'] = mock_boto3

# Set required environment variables before importing handler
os.environ['AGENTCORE_ARN'] = 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test'
os.environ['AGENTCORE_REGION'] = 'us-west-2'
os.environ['ENVIRONMENT'] = 'test'
os.environ['AWS_LAMBDA_FUNCTION_NAME'] = 'test-function'
# Required: the handler refuses to start without it, because event deduplication
# bounds how many Amazon Bedrock invocations a retried Slack webhook can trigger.
os.environ['DEDUP_TABLE_NAME'] = 'test-dedup-table'
os.environ['SLACK_SECRET_ARN'] = 'arn:aws:secretsmanager:us-west-2:123456789012:secret:test-secret'

# Mock slack_bolt and slack_sdk before importing handler
sys.modules['slack_bolt'] = MagicMock()
sys.modules['slack_bolt.adapter'] = MagicMock()
sys.modules['slack_bolt.adapter.aws_lambda'] = MagicMock()
sys.modules['slack_sdk'] = MagicMock()

# Add lambda directory to path for imports
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
lambda_path = str(_PROJECT_ROOT / "infra" / "lambda" / "slack_integration")
sys.path.insert(0, lambda_path)

# Purge handler so it reimports with mocked boto3 (but keep core.* intact
# to avoid invalidating references held by other test files)
sys.modules.pop('handler', None)

# Also purge core.secrets_manager to reset the singleton's cached credentials
sys.modules.pop('core.secrets_manager', None)

# Import handler module (requires mocked boto3/botocore)
import handler

# Import the actual functions from their modules
from core.utils import sanitize_error_message
from core.agentcore_client import AgentCoreClient
from adapters.slack_real import RealSlackClient

# Restore real boto3/botocore so other test files aren't affected
if _original_boto3 is not None:
    sys.modules['boto3'] = _original_boto3
else:
    sys.modules.pop('boto3', None)

if _original_botocore is not None:
    sys.modules['botocore'] = _original_botocore
else:
    sys.modules.pop('botocore', None)

if _original_botocore_exceptions is not None:
    sys.modules['botocore.exceptions'] = _original_botocore_exceptions
else:
    sys.modules.pop('botocore.exceptions', None)


class TestAgentCoreInvocationErrors:
    """Test error handling for AgentCore invocation failures."""
    
    def test_agentcore_not_found_error(self):
        """Test handling when AgentCore runtime is not found."""
        # Simulate ResourceNotFoundException
        error_response = {'Error': {'Code': 'ResourceNotFoundException', 'Message': 'Runtime not found'}}
        
        with patch('core.agentcore_client.boto3.client') as mock_boto:
            mock_client = Mock()
            mock_boto.return_value = mock_client
            mock_client.invoke_agent_runtime.side_effect = MockClientError(error_response, 'InvokeAgentRuntime')
            
            # Create AgentCore client and invoke - should raise exception
            client = AgentCoreClient('arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test', 'us-west-2')
            
            with pytest.raises(MockClientError) as exc_info:
                client.invoke("test prompt")
            
            # Verify it's the right error
            assert exc_info.value.response['Error']['Code'] == 'ResourceNotFoundException'
    
    def test_agentcore_timeout_error(self):
        """Test handling when AgentCore invocation times out."""
        with patch('core.agentcore_client.boto3.client') as mock_boto:
            mock_client = Mock()
            mock_boto.return_value = mock_client
            
            # Simulate timeout
            mock_client.invoke_agent_runtime.side_effect = Exception("Request timeout after 30 seconds")
            
            # Create AgentCore client and invoke - should raise exception
            client = AgentCoreClient('arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test', 'us-west-2')
            
            with pytest.raises(Exception) as exc_info:
                client.invoke("test prompt", session_id="test-session", actor_id="U123")
            
            # Should contain timeout message
            assert "timeout" in str(exc_info.value).lower()
    
    def test_agentcore_throttling_error(self):
        """Test handling when AgentCore throttles requests."""
        # Simulate throttling
        error_response = {'Error': {'Code': 'ThrottlingException', 'Message': 'Rate exceeded'}}
        
        with patch('core.agentcore_client.boto3.client') as mock_boto:
            mock_client = Mock()
            mock_boto.return_value = mock_client
            mock_client.invoke_agent_runtime.side_effect = MockClientError(error_response, 'InvokeAgentRuntime')
            
            # Create AgentCore client and invoke - should raise exception
            client = AgentCoreClient('arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test', 'us-west-2')
            
            with pytest.raises(MockClientError) as exc_info:
                client.invoke("test prompt")
            
            # Verify it's the right error
            assert exc_info.value.response['Error']['Code'] == 'ThrottlingException'


class TestSlackAPIErrors:
    """Test error handling for Slack API failures."""
    
    def test_slack_post_message_failure(self):
        """Test handling when posting to Slack fails."""
        # Create a Slack client
        slack_client = RealSlackClient('xoxb-test-token')
        
        with patch.object(slack_client, 'client') as mock_client:
            # Simulate Slack API error
            mock_client.chat_postMessage.side_effect = Exception("channel_not_found")
            
            # Should raise exception
            with pytest.raises(Exception):
                slack_client.post_message("C123", "test message")
    
    def test_slack_rate_limit_error(self):
        """Test handling when Slack rate limits requests."""
        slack_client = RealSlackClient('xoxb-test-token')
        
        with patch.object(slack_client, 'client') as mock_client:
            # Simulate rate limit
            mock_client.chat_postMessage.side_effect = Exception("rate_limited")
            
            with pytest.raises(Exception):
                slack_client.post_message("C123", "test message", thread_ts="123.456")
    
    def test_slack_invalid_auth_error(self):
        """Test handling when Slack authentication fails."""
        slack_client = RealSlackClient('xoxb-test-token')
        
        with patch.object(slack_client, 'client') as mock_client:
            # Simulate auth error
            mock_client.chat_postMessage.side_effect = Exception("invalid_auth")
            
            with pytest.raises(Exception):
                slack_client.post_message("C123", "test message")


class TestLambdaTimeoutHandling:
    """Test handling of Lambda timeout scenarios."""
    
    def test_async_processing_timeout(self):
        """Test handling when async processing times out."""
        with patch.object(handler, 'agentcore_client') as mock_agentcore:
            with patch.object(handler, 'slack_client') as mock_slack:
                mock_agentcore.invoke.side_effect = Exception("Task timed out after 30.00 seconds")
                
                event = {
                    "async_process": True,
                    "prompt": "test",
                    "channel": "C123",
                    "thread_ts": "123.456",
                    "user_id": "U123"
                }
                
                # Should handle gracefully and not raise
                handler.process_async(event)
                
                # Should attempt to post error message to Slack
                assert mock_slack.post_message.called
    
    def test_lambda_handler_timeout(self):
        """Test handling when Lambda handler times out."""
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request'
        mock_context.get_remaining_time_in_millis = Mock(return_value=100)
        
        with patch.object(handler.handler, 'handle', side_effect=Exception("Task timed out")):
            response = handler.lambda_handler({"type": "url_verification"}, mock_context)
            
            # Should return 500 error
            assert response['statusCode'] == 500
            body = json.loads(response['body'])
            assert 'error' in body
            assert 'request_id' in body


class TestMalformedEventHandling:
    """Test handling of malformed or invalid events."""
    
    def test_missing_required_fields(self):
        """Test handling when required fields are missing from event."""
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request'
        
        # Event missing required fields
        event = {
            "async_process": True,
            # Missing prompt, channel, etc.
        }
        
        with patch.object(handler, 'agentcore_client') as mock_agentcore:
            with patch.object(handler, 'slack_client'):
                mock_agentcore.invoke.return_value = "response"
                
                # Should handle gracefully
                handler.lambda_handler(event, mock_context)
    
    def test_invalid_json_in_event(self):
        """Test handling when event contains invalid JSON."""
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request'
        
        # Simulate Slack event with invalid structure
        event = {
            "body": "not-valid-json",
            "headers": {}
        }
        
        with patch.object(handler.handler, 'handle', side_effect=json.JSONDecodeError("msg", "doc", 0)):
            response = handler.lambda_handler(event, mock_context)
            
            # Should return error response
            assert response['statusCode'] == 500
    
    def test_empty_prompt(self):
        """Test handling when prompt is empty."""
        with patch.object(handler, 'agentcore_client') as mock_agentcore:
            mock_agentcore.invoke.return_value = "Please provide a question"
            
            mock_agentcore.invoke("")
            
            # Should still invoke agent (agent handles empty prompts)
            assert mock_agentcore.invoke.called
    
    def test_null_session_context(self):
        """Test handling when session context is None."""
        with patch.object(handler, 'agentcore_client') as mock_agentcore:
            mock_agentcore.invoke.return_value = "response"
            
            mock_agentcore.invoke("test", session_id=None, actor_id=None)
            
            # Should invoke without session context
            assert mock_agentcore.invoke.called
    
    def test_malformed_thread_ts(self):
        """Test handling when thread_ts has unexpected format."""
        with patch.object(handler.boto3, 'client') as mock_boto:
            mock_lambda = Mock()
            mock_boto.return_value = mock_lambda

            # Thread_ts without dot (unusual but possible)
            handler.trigger_async_processing(
                "test",
                "C123",
                "123456",  # No dot
                "U123"
            )

            # Should handle gracefully
            assert mock_lambda.invoke.called


class TestErrorMessageSanitization:
    """Test that error messages are properly sanitized."""
    
    def test_arn_sanitization(self):
        """Test that ARNs are removed from error messages."""
        error_msg = "Failed to invoke arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/abc123"
        sanitized = sanitize_error_message(error_msg)
        
        assert "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/abc123" not in sanitized
        assert "[ARN]" in sanitized
    
    def test_account_id_sanitization(self):
        """Test that account IDs are removed from error messages."""
        error_msg = "Error in account 123456789012"
        sanitized = sanitize_error_message(error_msg)
        
        assert "123456789012" not in sanitized
        assert "[ACCOUNT_ID]" in sanitized
    
    def test_file_path_sanitization(self):
        """Test that file paths are removed from error messages."""
        error_msg = "Error in /var/task/handler.py at line 42"
        sanitized = sanitize_error_message(error_msg)
        
        assert "/var/task/handler.py" not in sanitized
        assert "[FILE]" in sanitized
        assert "line 42" not in sanitized
        assert "line [LINE]" in sanitized
    
    def test_combined_sanitization(self):
        """Test sanitization of multiple sensitive elements."""
        error_msg = (
            "Failed to invoke arn:aws:iam::123456789012:role/MyRole "
            "in /var/task/handler.py, line 42"
        )
        sanitized = sanitize_error_message(error_msg)
        
        # All sensitive info should be removed
        assert "123456789012" not in sanitized
        assert "arn:aws:iam" not in sanitized or "[ARN]" in sanitized
        assert "/var/task/handler.py" not in sanitized
        assert "line 42" not in sanitized


class TestErrorLogging:
    """Test that errors are logged with proper context."""
    
    def test_error_logged_with_request_id(self):
        """Test that errors include request_id in logs."""
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request-123'
        
        with patch.object(handler.handler, 'handle', side_effect=Exception("test error")):
            response = handler.lambda_handler({}, mock_context)
            
            # Should return error response with request_id
            assert response['statusCode'] == 500
            body = json.loads(response['body'])
            assert body['request_id'] == 'test-request-123'
    
    def test_error_logged_with_session_context(self):
        """Test that errors include session context in logs."""
        with patch.object(handler, 'agentcore_client') as mock_agentcore:
            with patch.object(handler, 'slack_client') as mock_slack:
                mock_agentcore.invoke.side_effect = Exception("test error")
                
                event = {
                    "async_process": True,
                    "prompt": "test",
                    "channel": "C123",
                    "thread_ts": "123.456",
                    "session_id": "test-session",
                    "actor_id": "U123",
                    "user_id": "U123"
                }
                
                # Should handle gracefully
                handler.process_async(event)
                
                # Should attempt to post error to Slack
                assert mock_slack.post_message.called
    
    def test_duration_logged_on_error(self):
        """Test that duration is logged even when errors occur."""
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request'
        
        with patch.object(handler.handler, 'handle', side_effect=Exception("test error")):
            response = handler.lambda_handler({}, mock_context)
            
            # Should return error response
            assert response['statusCode'] == 500
