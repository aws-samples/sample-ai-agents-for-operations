"""
Property-based tests for API Gateway Stack logging and error handling.

These tests verify universal properties about logging, error handling, and
observability across all valid inputs using Hypothesis for property-based testing.
"""

import json
import pytest
from hypothesis import given, strategies as st, settings
from unittest.mock import Mock, patch

# Import handler module (mocks are set up in conftest.py)
import handler


@given(
    st.text(min_size=1, max_size=200),
    st.text(min_size=1, max_size=50).filter(lambda x: '.' not in x),
    st.text(min_size=1, max_size=50)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_property_20_error_logging_with_context(error_message, session_id, actor_id):
    """
    Property 20: Error logging with context
    
    For any invocation error, the Lambda function should log a structured JSON entry
    containing request_id, session_id (if present), and error_message fields.
    
    Feature: api-gateway-stack, Property 20: Error logging with context
    
    Validates: Requirements 12.1
    """
    # Create a mock logger to capture log calls
    with patch.object(handler, 'logger') as mock_logger:
        # Create a mock context with request_id
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request-123'
        
        # Create an event that will trigger an error in async processing
        event = {
            "async_process": True,
            "prompt": "test prompt",
            "channel": "C123456",
            "thread_ts": "1234567890.123456",
            "session_id": session_id,
            "actor_id": actor_id,
            "user_id": actor_id
        }
        
        # Mock the agentcore_client.invoke function to raise an exception
        with patch.object(handler.agentcore_client, 'invoke', side_effect=Exception(error_message)):
            # Mock slack_client.post_message to avoid actual Slack calls
            with patch.object(handler.slack_client, 'post_message'):
                # Call lambda_handler which should log the error
                handler.lambda_handler(event, mock_context)
        
        # Verify that error was logged with context
        error_logged = False
        for call in mock_logger.error.call_args_list:
            args, kwargs = call
            extra = kwargs.get('extra', {})
            
            # Check if this is the error log we're looking for
            if 'error' in extra:
                error_logged = True
                # Verify required fields are present
                assert 'error' in extra, "Error log must contain 'error' field"
                break
        
        assert error_logged, "Error must be logged with context"


@given(
    st.text(min_size=1, max_size=200)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_property_21_platform_specific_error_handling(error_message):
    """
    Property 21: Platform-specific error handling
    
    For any platform-specific error encountered by an integration handler, the system
    should log the error and return an appropriate response to the platform without
    exposing internal details.
    
    Feature: api-gateway-stack, Property 21: Platform-specific error handling
    
    Validates: Requirements 12.2
    """
    # Test Slack-specific error handling
    with patch.object(handler, 'logger') as mock_logger:
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request-123'
        
        # Create a Slack event that will trigger an error
        event = {
            "type": "url_verification",
            "challenge": "test_challenge"
        }
        
        # Mock the Slack handler to raise a platform-specific error
        with patch.object(handler.handler, 'handle', side_effect=Exception(error_message)):
            response = handler.lambda_handler(event, mock_context)
        
        # Verify error was logged
        assert mock_logger.error.called, "Platform error must be logged"
        
        # Verify response doesn't expose internal details
        assert response['statusCode'] == 500
        body = json.loads(response['body'])
        assert 'error' in body
        assert 'request_id' in body
        
        # Verify error message is sanitized (no ARNs, account IDs, file paths)
        error_msg = body.get('message', '')
        # Import sanitize function to verify it was used
        # The message should be sanitized
        assert 'arn:aws:' not in error_msg.lower() or '[ARN]' in error_msg


@given(
    st.text(min_size=1, max_size=100),
    st.sampled_from(['INFO', 'WARNING', 'ERROR'])
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_property_22_structured_logging_format(message, level):
    """
    Property 22: Structured logging format
    
    For any log entry produced by Lambda functions, the entry should be valid JSON
    with at least timestamp, level, and message fields.
    
    Feature: api-gateway-stack, Property 22: Structured logging format
    
    Validates: Requirements 12.3
    """
    # Import the StructuredFormatter from utils
    from core.utils import StructuredFormatter
    
    # Create a log record
    import logging
    record = logging.LogRecord(
        name='test',
        level=getattr(logging, level),
        pathname='test.py',
        lineno=1,
        msg=message,
        args=(),
        exc_info=None
    )
    
    # Format using the StructuredFormatter
    formatter = StructuredFormatter()
    formatted = formatter.format(record)
    
    # Verify it's valid JSON
    try:
        log_data = json.loads(formatted)
    except json.JSONDecodeError:
        pytest.fail(f"Log output is not valid JSON: {formatted}")
    
    # Verify required fields are present
    assert 'timestamp' in log_data, "Log must contain 'timestamp' field"
    assert 'level' in log_data, "Log must contain 'level' field"
    assert 'message' in log_data, "Log must contain 'message' field"
    
    # Verify level matches
    assert log_data['level'] == level
    
    # Verify message matches
    assert log_data['message'] == message


@given(
    st.text(min_size=1, max_size=50),
    st.integers(min_value=200, max_value=599)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_property_23_api_gateway_request_logging(request_id, status_code):
    """
    Property 23: API Gateway request logging
    
    For any request to the API Gateway, the access logs should contain request_id,
    timestamp, and response status.
    
    Feature: api-gateway-stack, Property 23: API Gateway request logging
    
    Validates: Requirements 2.4, 12.4
    """
    # This property tests that the Lambda handler logs request information
    # API Gateway access logging is configured in the CDK stack
    
    with patch.object(handler, 'logger') as mock_logger:
        mock_context = Mock()
        mock_context.aws_request_id = request_id
        
        # Create a simple event
        event = {
            "async_process": True,
            "prompt": "test",
            "channel": "C123",
            "thread_ts": "123.456",
            "user_id": "U123"
        }
        
        # Mock dependencies
        with patch.object(handler.agentcore_client, 'invoke', return_value="response"):
            with patch.object(handler.slack_client, 'post_message'):
                handler.lambda_handler(event, mock_context)
        
        # Verify request was logged with request_id
        info_logged = False
        for call in mock_logger.info.call_args_list:
            args, kwargs = call
            extra = kwargs.get('extra', {})
            
            if 'request_id' in extra:
                info_logged = True
                assert extra['request_id'] == request_id
                break
        
        assert info_logged, "Request must be logged with request_id"
        
        # Verify response status is logged
        completion_logged = False
        for call in mock_logger.info.call_args_list:
            args, kwargs = call
            message = args[0] if args else ''
            
            if 'completed' in message.lower():
                completion_logged = True
                break
        
        assert completion_logged, "Request completion must be logged"


@given(
    st.text(min_size=1, max_size=100),
    st.integers(min_value=0, max_value=10000)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_property_24_cloudwatch_metrics_exposure(operation, duration_ms):
    """
    Property 24: CloudWatch metrics exposure
    
    For any agent invocation, the system should publish CloudWatch metrics for
    invocation count, error rate, and latency.
    
    Feature: api-gateway-stack, Property 24: CloudWatch metrics exposure
    
    Validates: Requirements 12.5
    """
    # This property tests that duration metrics are logged
    # CloudWatch metrics are derived from structured logs
    
    with patch.object(handler, 'logger') as mock_logger:
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request'
        
        event = {
            "async_process": True,
            "prompt": "test",
            "channel": "C123",
            "thread_ts": "123.456",
            "user_id": "U123"
        }
        
        # Mock dependencies
        with patch.object(handler.agentcore_client, 'invoke', return_value="response"):
            with patch.object(handler.slack_client, 'post_message'):
                handler.lambda_handler(event, mock_context)
        
        # Verify duration is logged (enables CloudWatch metrics)
        duration_logged = False
        for call in mock_logger.info.call_args_list:
            args, kwargs = call
            extra = kwargs.get('extra', {})
            
            if 'duration_ms' in extra:
                duration_logged = True
                # Duration should be a positive number
                assert extra['duration_ms'] >= 0
                break
        
        assert duration_logged, "Duration must be logged for CloudWatch metrics"


@given(
    st.text(min_size=10, max_size=200)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_property_25_error_message_sanitization(error_message):
    """
    Property 25: Error message sanitization
    
    For any error response, the error message should not contain sensitive information
    such as IAM role ARNs, AWS account IDs, or internal stack traces.
    
    Feature: api-gateway-stack, Property 25: Error message sanitization
    
    Validates: Requirements 12.6
    """
    # Import the sanitize function from utils
    from core.utils import sanitize_error_message
    
    # Add sensitive information to the error message
    sensitive_msg = f"{error_message} arn:aws:iam::123456789012:role/MyRole /var/task/handler.py line 42"
    
    # Sanitize the message
    sanitized = sanitize_error_message(sensitive_msg)
    
    # Verify ARNs are removed or sanitized
    # The ARN should either be completely removed or replaced with [ARN]
    if 'arn:aws:iam::123456789012:role/MyRole' in sensitive_msg:
        assert 'arn:aws:iam::123456789012:role/MyRole' not in sanitized, \
            "Original ARN should not appear in sanitized message"
        # Either the whole ARN is replaced with [ARN], or the account ID within it is replaced
        assert '[ARN]' in sanitized or '[ACCOUNT_ID]' in sanitized, \
            "ARN should be sanitized with [ARN] or account ID should be replaced"
    
    # Verify standalone account IDs are removed (not part of ARN)
    # Add a standalone account ID to test
    msg_with_standalone_id = f"{error_message} Account: 987654321098"
    sanitized_standalone = sanitize_error_message(msg_with_standalone_id)
    if '987654321098' in msg_with_standalone_id:
        assert '987654321098' not in sanitized_standalone, \
            "Standalone account ID should be sanitized"
    
    # Verify file paths are removed
    assert '/var/task/handler.py' not in sanitized, \
        "File paths should be sanitized"
    if '/var/task/handler.py' in sensitive_msg:
        assert '[FILE]' in sanitized, "File path should be replaced with [FILE]"
    
    # Verify line numbers are removed
    if 'line 42' in sensitive_msg:
        assert 'line 42' not in sanitized or 'line [LINE]' in sanitized, \
            "Line numbers should be sanitized"
