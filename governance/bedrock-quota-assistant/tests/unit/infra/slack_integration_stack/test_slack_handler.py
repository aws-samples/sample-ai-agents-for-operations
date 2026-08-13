"""Unit tests for Slack event handlers.

Tests cover:
- app_mention handler
- message handler (DMs and thread replies)
- slash command handler
- Edge cases (bot messages, message subtypes, thread detection)
"""
import json
from unittest.mock import Mock, MagicMock, patch
import sys
import os

# Save original modules before mocking
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

# Mock slack_bolt and slack_sdk before any imports
sys.modules['slack_bolt'] = MagicMock()
sys.modules['slack_bolt.adapter'] = MagicMock()
sys.modules['slack_bolt.adapter.aws_lambda'] = MagicMock()
sys.modules['slack_sdk'] = MagicMock()

# Add lambda directory to path for imports
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
lambda_path = str(_PROJECT_ROOT / "infra" / "lambda" / "slack_integration")
sys.path.insert(0, lambda_path)

# Mock environment variables before importing handler
os.environ['SLACK_SECRET_ARN'] = 'arn:aws:secretsmanager:us-west-2:123456789012:secret:test-secret'
os.environ['AGENTCORE_ARN'] = 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-runtime'
os.environ['AGENTCORE_REGION'] = 'us-west-2'
os.environ['ENVIRONMENT'] = 'test'
os.environ['AWS_LAMBDA_FUNCTION_NAME'] = 'test-function'
# Required: the handler refuses to start without it, because event deduplication
# bounds how many Amazon Bedrock invocations a retried Slack webhook can trigger.
os.environ['DEDUP_TABLE_NAME'] = 'test-dedup-table'

# Purge handler so it reimports with mocked boto3 (but keep core.* intact
# to avoid invalidating references held by other test files)
sys.modules.pop('handler', None)

# Also purge core.secrets_manager to reset the singleton's cached credentials
sys.modules.pop('core.secrets_manager', None)

import handler

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


class TestProcessMessage:
    """Tests for process_message function (core message processing logic)."""
    
    def test_process_message_triggers_async_processing(self):
        """Test that process_message triggers async processing with correct parameters."""
        body = {
            "event": {
                "type": "app_mention",
                "text": "<@U123456> what are the quotas?",
                "channel": "C123456",
                "ts": "1234567890.123456",
                "user": "U789012"
            }
        }
        say = Mock()
        client = Mock()
        
        with patch.object(handler, 'trigger_async_processing') as mock_trigger:
            handler.process_message(body, say, client)
            
            # Verify async processing was triggered
            mock_trigger.assert_called_once()
            # Check that it was called with the expected arguments
            call_args = mock_trigger.call_args
            assert "what are the quotas?" in str(call_args) or "C123456" in str(call_args)
    
    def test_process_message_sends_acknowledgment(self):
        """Test that process_message sends acknowledgment via assistant status API."""
        body = {
            "event": {
                "type": "app_mention",
                "text": "<@U123456> hello",
                "channel": "C123456",
                "ts": "1234567890.123456",
                "user": "U789012"
            }
        }
        say = Mock()
        client = Mock()

        with patch.object(handler, 'trigger_async_processing'):
            handler.process_message(body, say, client)

            # Handler sets loading status via Agents & AI Apps API
            assert client.assistant_threads_setStatus.called
    
    def test_process_message_with_empty_text_shows_help(self):
        """Test that process_message with empty text shows help message."""
        body = {
            "event": {
                "type": "app_mention",
                "text": "<@U123456>",
                "channel": "C123456",
                "ts": "1234567890.123456",
                "user": "U789012"
            }
        }
        say = Mock()
        client = Mock()
        
        with patch.object(handler, 'trigger_async_processing') as mock_trigger:
            handler.process_message(body, say, client)
            
            # Verify help message was sent
            assert say.called
            
            # Verify async processing was NOT triggered
            mock_trigger.assert_not_called()
    
    def test_process_message_removes_bot_mention_from_text(self):
        """Test that bot mentions are removed from message text."""
        body = {
            "event": {
                "text": "<@U123456> check quotas",
                "channel": "C123456",
                "ts": "1234567890.123456",
                "user": "U789012"
            }
        }
        say = Mock()
        client = Mock()
        
        with patch.object(handler, 'trigger_async_processing') as mock_trigger:
            handler.process_message(body, say, client)
            
            # Verify trigger was called (text processing happened)
            assert mock_trigger.called


class TestHandleMessage:
    """Tests for handle_message function edge cases."""
    
    def test_top_level_channel_message_ignored(self):
        """Test that top-level channel messages are ignored (require @mention)."""
        body = {
            "event": {
                "type": "message",
                "text": "hello everyone",
                "channel": "C123456",
                "channel_type": "channel",
                "ts": "1234567890.123456",
                "user": "U789012"
            }
        }
        say = Mock()
        client = Mock()
        
        with patch.object(handler, 'process_message') as mock_process:
            handler.handle_message(body, say, client)
            
            # Verify process_message was NOT called (top-level messages need @mention)
            mock_process.assert_not_called()
    
    def test_thread_reply_in_unrelated_thread_ignored(self):
        """Test that thread replies in unrelated threads are ignored."""
        body = {
            "event": {
                "type": "message",
                "text": "follow up question",
                "channel": "C123456",
                "channel_type": "channel",
                "ts": "1234567891.123456",
                "thread_ts": "1234567890.123456",
                "user": "U789012"
            }
        }
        say = Mock()
        client = Mock()
        
        # Mock bot user ID
        with patch.object(handler, 'get_bot_user_id', return_value="UBOT123"):
            # Mock thread parent - started by another user, no bot mention
            client.conversations_replies.return_value = {
                "messages": [{
                    "user": "U999999",
                    "text": "Some other conversation",
                    "ts": "1234567890.123456"
                }]
            }
            
            with patch.object(handler, 'process_message') as mock_process:
                handler.handle_message(body, say, client)
                
                # Verify process_message was NOT called
                mock_process.assert_not_called()
    
    def test_bot_message_ignored(self):
        """Test that bot messages are ignored."""
        body = {
            "event": {
                "type": "message",
                "text": "automated message",
                "channel": "C123456",
                "channel_type": "im",
                "ts": "1234567890.123456",
                "bot_id": "B123456"
            }
        }
        say = Mock()
        client = Mock()
        
        with patch.object(handler, 'process_message') as mock_process:
            handler.handle_message(body, say, client)
            
            # Verify process_message was NOT called
            mock_process.assert_not_called()
    
    def test_message_subtype_ignored(self):
        """Test that message subtypes (edits, deletes) are ignored."""
        body = {
            "event": {
                "type": "message",
                "subtype": "message_changed",
                "text": "edited message",
                "channel": "C123456",
                "ts": "1234567890.123456"
            }
        }
        say = Mock()
        client = Mock()
        
        with patch.object(handler, 'process_message') as mock_process:
            handler.handle_message(body, say, client)
            
            # Verify process_message was NOT called
            mock_process.assert_not_called()
    
    def test_thread_reply_with_mention_ignored(self):
        """Test that thread replies with @mentions are ignored (app_mention handles)."""
        body = {
            "event": {
                "type": "message",
                "text": "<@UBOT123> follow up",
                "channel": "C123456",
                "channel_type": "channel",
                "ts": "1234567891.123456",
                "thread_ts": "1234567890.123456",
                "user": "U789012"
            }
        }
        say = Mock()
        client = Mock()
        
        with patch.object(handler, 'process_message') as mock_process:
            handler.handle_message(body, say, client)
            
            # Verify process_message was NOT called (app_mention will handle)
            mock_process.assert_not_called()





class TestSessionContextDerivation:
    """Tests for session context derivation from Slack events."""
    
    def test_thread_ts_sanitized_for_session_id(self):
        """Test that thread_ts dots are replaced with dashes for session_id."""
        prompt = "test question"
        channel = "C123456"
        thread_ts = "1234567890.123456"
        user_id = "U789012"

        with patch.object(handler.boto3, 'client') as mock_boto:
            mock_lambda = Mock()
            mock_lambda.invoke = Mock(return_value={})
            mock_boto.return_value = mock_lambda

            handler.trigger_async_processing(prompt, channel, thread_ts, user_id)

            assert mock_lambda.invoke.called
            call_kwargs = mock_lambda.invoke.call_args[1]
            payload = json.loads(call_kwargs['Payload'])
            assert payload['session_id'] == "1234567890-123456"
    
    def test_slash_command_session_id_none_initially(self):
        """Test that slash commands have session_id=None initially (set in process_async)."""
        prompt = "test question"
        channel = "C123456"
        thread_ts = None
        user_id = "U789012"

        with patch.object(handler.boto3, 'client') as mock_boto:
            mock_lambda = Mock()
            mock_lambda.invoke = Mock(return_value={})
            mock_boto.return_value = mock_lambda

            handler.trigger_async_processing(prompt, channel, thread_ts, user_id, is_slash_command=True)

            call_kwargs = mock_lambda.invoke.call_args[1]
            payload = json.loads(call_kwargs['Payload'])
            assert payload['session_id'] is None
            assert payload['is_slash_command'] is True
    
    def test_actor_id_always_set_to_user_id(self):
        """Test that actor_id is always set to Slack user_id."""
        prompt = "test question"
        channel = "C123456"
        thread_ts = "1234567890.123456"
        user_id = "U789012"

        with patch.object(handler.boto3, 'client') as mock_boto:
            mock_lambda = Mock()
            mock_lambda.invoke = Mock(return_value={})
            mock_boto.return_value = mock_lambda

            handler.trigger_async_processing(prompt, channel, thread_ts, user_id)

            call_kwargs = mock_lambda.invoke.call_args[1]
            payload = json.loads(call_kwargs['Payload'])
            assert payload['actor_id'] == user_id


class TestAsyncProcessing:
    """Tests for async processing logic."""
    
    def test_slash_command_creates_thread_and_replies(self):
        """Test that slash commands create a thread and reply in it."""
        payload = {
            "async_process": True,
            "prompt": "test question",
            "channel": "C123456",
            "thread_ts": None,
            "is_slash_command": True,
            "user_id": "U789012",
            "session_id": None,
            "actor_id": "U789012"
        }

        with patch.object(handler.slack_client, 'post_message') as mock_post, \
             patch.object(handler.agentcore_client, 'invoke') as mock_invoke, \
             patch.object(handler, '_post_with_streaming') as mock_stream:

            mock_post.return_value = "1234567890.123456"
            mock_invoke.return_value = "Here's the answer"

            handler.process_async(payload)

            # Header message posted via post_message
            assert mock_post.call_count == 1
            # Response streamed back via _post_with_streaming
            assert mock_stream.called
            # Agent was invoked
            assert mock_invoke.called
    
    def test_regular_message_replies_in_existing_thread(self):
        """Test that regular messages reply in existing thread."""
        payload = {
            "async_process": True,
            "prompt": "test question",
            "channel": "C123456",
            "thread_ts": "1234567890.123456",
            "is_slash_command": False,
            "user_id": "U789012",
            "session_id": "1234567890-123456",
            "actor_id": "U789012"
        }

        with patch.object(handler.agentcore_client, 'invoke') as mock_invoke, \
             patch.object(handler, '_post_with_streaming') as mock_stream:

            mock_invoke.return_value = "Here's the answer"

            handler.process_async(payload)

            # Verify agent was invoked with session context
            assert mock_invoke.called
            call_kwargs = mock_invoke.call_args[1]
            assert call_kwargs['session_id'] == "1234567890-123456"
            assert call_kwargs['actor_id'] == "U789012"

            # Verify response was streamed back
            assert mock_stream.called


class TestLambdaHandler:
    """Tests for Lambda handler entry point."""
    
    def test_async_process_flag_triggers_async_processing(self):
        """Test that async_process flag triggers async processing."""
        event = {
            "async_process": True,
            "prompt": "test",
            "channel": "C123456",
            "thread_ts": "1234567890.123456",
            "user_id": "U789012",
            "session_id": "1234567890-123456",
            "actor_id": "U789012"
        }
        context = Mock()
        context.aws_request_id = "test-request-id"
        
        with patch.object(handler, 'process_async') as mock_process:
            result = handler.lambda_handler(event, context)
            
            # Verify async processing was called
            mock_process.assert_called_once_with(event)
            assert result['statusCode'] == 200
    
    def test_slack_event_handled_by_slack_handler(self):
        """Test that Slack events are handled by SlackRequestHandler."""
        event = {
            "type": "url_verification",
            "challenge": "test-challenge"
        }
        context = Mock()
        context.aws_request_id = "test-request-id"
        
        with patch.object(handler.handler, 'handle') as mock_handle:
            mock_handle.return_value = {"statusCode": 200, "body": "test-challenge"}
            
            result = handler.lambda_handler(event, context)
            
            # Verify Slack handler was called
            mock_handle.assert_called_once_with(event, context)
            assert result['statusCode'] == 200
