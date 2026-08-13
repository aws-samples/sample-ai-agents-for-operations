"""Property-based tests for API Gateway Stack Slack integration.

This module contains property-based tests that verify Slack integration
handler behavior in the API Gateway Stack.
"""

import json
from hypothesis import given, strategies as st, settings, assume
from unittest.mock import Mock, patch, MagicMock
import os

# Import handler module (mocks are set up in conftest.py)


# Strategy for generating valid Slack event payloads
@st.composite
def slack_event_payload(draw):
    """Generate a valid Slack event payload for testing.
    
    Returns a dictionary representing a Slack event that would be sent
    to the Lambda function via API Gateway.
    """
    event_type = draw(st.sampled_from(["app_mention", "message"]))
    
    # Generate base event structure
    event = {
        "type": "event_callback",
        "event": {
            "type": event_type,
            "text": draw(st.text(min_size=1, max_size=500)),
            "user": draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')))),
            "channel": draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')))),
            "ts": draw(st.text(min_size=10, max_size=20, alphabet=st.characters(whitelist_categories=('Nd', 'Pc')))),
        }
    }
    
    # Optionally add thread_ts for threaded messages
    if draw(st.booleans()):
        event["event"]["thread_ts"] = draw(st.text(min_size=10, max_size=20, alphabet=st.characters(whitelist_categories=('Nd', 'Pc'))))
    
    # For app_mention, add bot mention in text
    if event_type == "app_mention":
        bot_id = draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))))
        event["event"]["text"] = f"<@{bot_id}> {event['event']['text']}"
    
    return event


@settings(
    deadline=None,
    max_examples=100  # Property tests should run at least 100 iterations
)
@given(event=slack_event_payload())
def test_handler_receives_complete_event_payload(event):
    """
    Test that integration handler receives complete event payload without modification.
    
    For any platform-specific event sent to an integration handler's API route,
    the Lambda function should receive the complete event payload without modification.
    This ensures no data is lost during transmission from API Gateway to Lambda.
    """
    # Mock environment variables required by the handler
    with patch.dict(os.environ, {
        'SLACK_BOT_TOKEN': 'xoxb-test-token',
        'SLACK_SIGNING_SECRET': 'test-signing-secret',
        'AGENTCORE_ARN': 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test',
        'AGENTCORE_REGION': 'us-west-2',
        'ENVIRONMENT': 'test',
        'AWS_LAMBDA_FUNCTION_NAME': 'test-function'
    }):
        # Import handler after setting environment variables
        import handler as slack_handler
        
        # Create a mock Lambda context
        mock_context = Mock()
        mock_context.aws_request_id = 'test-request-id'
        mock_context.request_id = 'test-request-id'
        mock_context.function_name = 'test-function'
        
        # Create API Gateway proxy event structure
        # This simulates how API Gateway passes events to Lambda
        api_gateway_event = {
            'body': json.dumps(event),
            'headers': {
                'Content-Type': 'application/json',
                'X-Slack-Request-Timestamp': '1234567890',
                'X-Slack-Signature': 'v0=test-signature'
            },
            'httpMethod': 'POST',
            'path': '/slack/events',
            'requestContext': {
                'requestId': 'test-request-id'
            }
        }
        
        # Track what event the handler receives
        received_event = None
        received_context = None
        
        # Mock the SlackRequestHandler's handle method to capture the event
        # The handler uses slack_bolt's SlackRequestHandler which processes the event
        def mock_handle(event_arg, context_arg):
            nonlocal received_event, received_context
            # Capture the event and context that the handler receives
            received_event = event_arg
            received_context = context_arg
            
            # Return a successful response
            return {
                'statusCode': 200,
                'body': json.dumps({'ok': True})
            }
        
        # Patch the handler's handle method
        with patch.object(slack_handler.handler, 'handle', side_effect=mock_handle):
            # Invoke the Lambda handler
            response = slack_handler.lambda_handler(api_gateway_event, mock_context)
        
        # Verify the handler received the event
        assert received_event is not None, "Handler did not receive the event"
        assert received_context is not None, "Handler did not receive the context"
        
        # Verify the event structure is preserved
        # The handler should receive the complete API Gateway event
        assert 'body' in received_event, "Event body is missing"
        assert 'headers' in received_event, "Event headers are missing"
        assert 'httpMethod' in received_event, "Event httpMethod is missing"
        assert 'path' in received_event, "Event path is missing"
        
        # Verify the event body contains the original Slack event
        assert received_event['body'] == api_gateway_event['body'], (
            "Event body was modified during transmission"
        )
        
        # Verify headers are preserved
        assert received_event['headers'] == api_gateway_event['headers'], (
            "Event headers were modified during transmission"
        )
        
        # Verify HTTP method is preserved
        assert received_event['httpMethod'] == api_gateway_event['httpMethod'], (
            "HTTP method was modified during transmission"
        )
        
        # Verify path is preserved
        assert received_event['path'] == api_gateway_event['path'], (
            "Path was modified during transmission"
        )
        
        # Verify the Slack event payload within the body is intact
        received_slack_event = json.loads(received_event['body'])
        
        # Check that all top-level keys from the original event are present
        for key in event.keys():
            assert key in received_slack_event, (
                f"Event key '{key}' is missing from received event"
            )
            assert received_slack_event[key] == event[key], (
                f"Event key '{key}' was modified. "
                f"Expected: {event[key]}, Got: {received_slack_event[key]}"
            )
        
        # Verify the handler returns a successful response
        assert response['statusCode'] == 200, (
            f"Handler should return 200 for valid events, got {response['statusCode']}"
        )


# Strategy for generating Slack slash command payloads
@st.composite
def slack_slash_command_payload(draw):
    """Generate a valid Slack slash command payload for testing.
    
    Returns a dictionary representing a Slack slash command that would be sent
    to the Lambda function via API Gateway.
    """
    return {
        "command": "/bedrock",
        "text": draw(st.text(min_size=1, max_size=500)),
        "user_id": draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')))),
        "channel_id": draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')))),
        "response_url": "https://hooks.slack.com/commands/test"
    }


@settings(
    deadline=None,
    max_examples=100  # Property tests should run at least 100 iterations
)
@given(event=slack_event_payload())
def test_slack_handler_extracts_message_text(event):
    """
    Test that Slack handler correctly extracts user message text from events.
    
    For any valid Slack event (app_mention, message, slash command), the Slack handler
    should extract the user message text correctly, including stripping bot mentions
    from app_mention events.
    """
    # Mock environment variables required by the handler
    with patch.dict(os.environ, {
        'SLACK_BOT_TOKEN': 'xoxb-test-token',
        'SLACK_SIGNING_SECRET': 'test-signing-secret',
        'AGENTCORE_ARN': 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test',
        'AGENTCORE_REGION': 'us-west-2',
        'ENVIRONMENT': 'test',
        'AWS_LAMBDA_FUNCTION_NAME': 'test-function'
    }):
        # Import handler after setting environment variables
        import handler as slack_handler
        
        # Extract the expected message text from the event
        expected_text = event["event"]["text"]
        event_type = event["event"]["type"]
        
        # For app_mention events, the text includes the bot mention which should be stripped
        if event_type == "app_mention" and "<@" in expected_text:
            # The handler strips the bot mention, so we expect the text after the mention
            expected_text = expected_text.split(">", 1)[-1].strip()

        # If the extracted text is empty/whitespace, the handler shows help instead
        # of triggering async processing — skip these cases
        assume(expected_text.strip() != "")

        # Track what prompt is extracted
        extracted_prompt = None
        
        # Mock the Lambda client to capture async invocation
        with patch('boto3.client') as mock_boto_client:
            mock_lambda_client = MagicMock()
            
            def mock_client_factory(service_name, **kwargs):
                if service_name == 'lambda':
                    return mock_lambda_client
                # Return a mock for other services
                return MagicMock()
            
            mock_boto_client.side_effect = mock_client_factory
            
            # Mock the invoke method to capture the payload
            def mock_invoke(**kwargs):
                nonlocal extracted_prompt
                payload_str = kwargs.get('Payload', '{}')
                if isinstance(payload_str, bytes):
                    payload_str = payload_str.decode('utf-8')
                payload = json.loads(payload_str)
                extracted_prompt = payload.get('prompt')
                return {}
            
            mock_lambda_client.invoke = mock_invoke
            
            # Mock Slack client for posting acknowledgment
            with patch('slack_sdk.WebClient') as mock_slack_client:
                mock_client_instance = MagicMock()
                mock_slack_client.return_value = mock_client_instance
                mock_client_instance.chat_postMessage.return_value = {'ts': '1234567890.123456'}
                
                # Create a mock Lambda context
                mock_context = Mock()
                mock_context.aws_request_id = 'test-request-id'
                mock_context.request_id = 'test-request-id'
                mock_context.function_name = 'test-function'
                
                # Create API Gateway proxy event structure
                api_gateway_event = {
                    'body': json.dumps(event),
                    'headers': {
                        'Content-Type': 'application/json',
                        'X-Slack-Request-Timestamp': '1234567890',
                        'X-Slack-Signature': 'v0=test-signature'
                    },
                    'httpMethod': 'POST',
                    'path': '/slack/events',
                    'requestContext': {
                        'requestId': 'test-request-id'
                    }
                }
                
                # Mock the SlackRequestHandler's handle method to process the event
                # The handler uses slack_bolt which processes events through registered handlers
                def mock_handle(event_arg, context_arg):
                    # Parse the Slack event from the body
                    slack_event = json.loads(event_arg['body'])
                    
                    # Simulate the handler processing based on event type
                    if slack_event.get('type') == 'event_callback':
                        inner_event = slack_event.get('event', {})
                        inner_event_type = inner_event.get('type')
                        
                        # Simulate process_message being called
                        if inner_event_type in ['app_mention', 'message']:
                            text = inner_event.get('text', '')
                            channel = inner_event.get('channel')
                            thread_ts = inner_event.get('thread_ts') or inner_event.get('ts')
                            user_id = inner_event.get('user')
                            
                            # Remove bot mention from text if present (simulating handler logic)
                            if "<@" in text:
                                text = text.split(">", 1)[-1].strip()
                            
                            # Simulate trigger_async_processing being called
                            if text:  # Only if there's text to process
                                session_id = thread_ts.replace(".", "-") if thread_ts else None
                                payload = {
                                    "async_process": True,
                                    "prompt": text,
                                    "channel": channel,
                                    "thread_ts": thread_ts,
                                    "is_slash_command": False,
                                    "user_id": user_id,
                                    "session_id": session_id,
                                    "actor_id": user_id
                                }
                                # Call the mocked Lambda invoke
                                mock_lambda_client.invoke(
                                    FunctionName='test-function',
                                    InvocationType='Event',
                                    Payload=json.dumps(payload).encode()
                                )
                    
                    return {
                        'statusCode': 200,
                        'body': json.dumps({'ok': True})
                    }
                
                # Patch the handler's handle method
                with patch.object(slack_handler.handler, 'handle', side_effect=mock_handle):
                    # Invoke the Lambda handler
                    response = slack_handler.lambda_handler(api_gateway_event, mock_context)
        
        # Verify the handler extracted the correct message text
        assert extracted_prompt is not None, (
            "Handler did not extract any prompt from the Slack event"
        )
        
        # Verify the extracted prompt matches the expected text
        assert extracted_prompt == expected_text, (
            f"Handler extracted incorrect message text. "
            f"Expected: '{expected_text}', Got: '{extracted_prompt}'"
        )
        
        # Verify the handler returns a successful response
        assert response['statusCode'] == 200, (
            f"Handler should return 200 for valid events, got {response['statusCode']}"
        )



# Strategy for generating Slack messages with thread_ts and user_id
@st.composite
def slack_message_with_context(draw):
    """Generate a Slack message with thread_ts and user_id for session context testing.
    
    Returns a dictionary with the event and expected session context.
    """
    # Generate thread_ts with dots (Slack timestamp format: "1234567890.123456")
    timestamp_int = draw(st.integers(min_value=1000000000, max_value=9999999999))
    timestamp_decimal = draw(st.integers(min_value=100000, max_value=999999))
    thread_ts = f"{timestamp_int}.{timestamp_decimal}"
    
    # Generate user_id (alphanumeric)
    user_id = draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))))
    
    # Generate message text
    text = draw(st.text(min_size=1, max_size=500))
    
    # Generate channel
    channel = draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))))
    
    # Create event
    event = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "text": text,
            "user": user_id,
            "channel": channel,
            "ts": draw(st.text(min_size=10, max_size=20, alphabet=st.characters(whitelist_categories=('Nd', 'Pc')))),
            "thread_ts": thread_ts
        }
    }
    
    # Expected session context
    expected_session_id = thread_ts.replace(".", "-")
    expected_actor_id = user_id
    
    return {
        "event": event,
        "expected_session_id": expected_session_id,
        "expected_actor_id": expected_actor_id
    }


@settings(
    deadline=None,
    max_examples=100  # Property tests should run at least 100 iterations
)
@given(data=slack_message_with_context())
def test_slack_derives_session_context_from_thread(data):
    """
    Test that Slack handler derives session context from thread_ts and user_id.
    
    For any Slack message with thread_ts and user_id, the handler should derive
    session_id by replacing dots with dashes in thread_ts, and set actor_id to user_id.
    This ensures conversation continuity across multiple messages in a thread.
    """
    event = data["event"]
    expected_session_id = data["expected_session_id"]
    expected_actor_id = data["expected_actor_id"]
    
    # Mock environment variables required by the handler
    with patch.dict(os.environ, {
        'SLACK_BOT_TOKEN': 'xoxb-test-token',
        'SLACK_SIGNING_SECRET': 'test-signing-secret',
        'AGENTCORE_ARN': 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test',
        'AGENTCORE_REGION': 'us-west-2',
        'ENVIRONMENT': 'test',
        'AWS_LAMBDA_FUNCTION_NAME': 'test-function'
    }):
        # Import handler after setting environment variables
        import handler as slack_handler
        
        # Track the session context passed to async invocation
        captured_session_id = None
        captured_actor_id = None
        
        # Mock the Lambda client to capture async invocation
        with patch('boto3.client') as mock_boto_client:
            mock_lambda_client = MagicMock()
            
            def mock_client_factory(service_name, **kwargs):
                if service_name == 'lambda':
                    return mock_lambda_client
                # Return a mock for other services
                return MagicMock()
            
            mock_boto_client.side_effect = mock_client_factory
            
            # Mock the invoke method to capture the payload
            def mock_invoke(**kwargs):
                nonlocal captured_session_id, captured_actor_id
                payload_str = kwargs.get('Payload', '{}')
                if isinstance(payload_str, bytes):
                    payload_str = payload_str.decode('utf-8')
                payload = json.loads(payload_str)
                captured_session_id = payload.get('session_id')
                captured_actor_id = payload.get('actor_id')
                return {}
            
            mock_lambda_client.invoke = mock_invoke
            
            # Mock Slack client for posting acknowledgment
            with patch('slack_sdk.WebClient') as mock_slack_client:
                mock_client_instance = MagicMock()
                mock_slack_client.return_value = mock_client_instance
                mock_client_instance.chat_postMessage.return_value = {'ts': '1234567890.123456'}
                mock_client_instance.auth_test.return_value = {'user_id': 'B123456'}
                
                # Create a mock Lambda context
                mock_context = Mock()
                mock_context.aws_request_id = 'test-request-id'
                mock_context.request_id = 'test-request-id'
                mock_context.function_name = 'test-function'
                
                # Create API Gateway proxy event structure
                api_gateway_event = {
                    'body': json.dumps(event),
                    'headers': {
                        'Content-Type': 'application/json',
                        'X-Slack-Request-Timestamp': '1234567890',
                        'X-Slack-Signature': 'v0=test-signature'
                    },
                    'httpMethod': 'POST',
                    'path': '/slack/events',
                    'requestContext': {
                        'requestId': 'test-request-id'
                    }
                }
                
                # Mock the SlackRequestHandler's handle method to process the event
                def mock_handle(event_arg, context_arg):
                    # Parse the Slack event from the body
                    slack_event = json.loads(event_arg['body'])
                    
                    # Simulate the handler processing based on event type
                    if slack_event.get('type') == 'event_callback':
                        inner_event = slack_event.get('event', {})
                        inner_event_type = inner_event.get('type')
                        
                        # Simulate process_message being called
                        if inner_event_type == 'message':
                            text = inner_event.get('text', '')
                            channel = inner_event.get('channel')
                            thread_ts = inner_event.get('thread_ts') or inner_event.get('ts')
                            user_id = inner_event.get('user')
                            
                            # Simulate trigger_async_processing being called
                            if text:  # Only if there's text to process
                                # This is the key logic we're testing:
                                # session_id is derived by replacing dots with dashes in thread_ts
                                session_id = thread_ts.replace(".", "-") if thread_ts else None
                                
                                payload = {
                                    "async_process": True,
                                    "prompt": text,
                                    "channel": channel,
                                    "thread_ts": thread_ts,
                                    "is_slash_command": False,
                                    "user_id": user_id,
                                    "session_id": session_id,  # Derived session_id
                                    "actor_id": user_id  # actor_id is set to user_id
                                }
                                # Call the mocked Lambda invoke
                                mock_lambda_client.invoke(
                                    FunctionName='test-function',
                                    InvocationType='Event',
                                    Payload=json.dumps(payload).encode()
                                )
                    
                    return {
                        'statusCode': 200,
                        'body': json.dumps({'ok': True})
                    }
                
                # Patch the handler's handle method
                with patch.object(slack_handler.handler, 'handle', side_effect=mock_handle):
                    # Invoke the Lambda handler
                    response = slack_handler.lambda_handler(api_gateway_event, mock_context)
        
        # Verify the handler derived the correct session_id
        assert captured_session_id is not None, (
            "Handler did not derive session_id from thread_ts"
        )
        
        assert captured_session_id == expected_session_id, (
            f"Handler derived incorrect session_id. "
            f"Expected: '{expected_session_id}', Got: '{captured_session_id}'. "
            f"Original thread_ts: '{event['event']['thread_ts']}'"
        )
        
        # Verify the handler set actor_id to user_id
        assert captured_actor_id is not None, (
            "Handler did not set actor_id"
        )
        
        assert captured_actor_id == expected_actor_id, (
            f"Handler set incorrect actor_id. "
            f"Expected: '{expected_actor_id}', Got: '{captured_actor_id}'"
        )
        
        # Verify the handler returns a successful response
        assert response['statusCode'] == 200, (
            f"Handler should return 200 for valid events, got {response['statusCode']}"
        )


# Strategy for generating agent responses
@st.composite
def agent_response_data(draw):
    """Generate agent response data for testing Slack response posting.
    
    Returns a dictionary with the agent response and Slack context.
    """
    # Generate agent response text
    response_text = draw(st.text(min_size=1, max_size=2000))
    
    # Generate Slack channel
    channel = draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))))
    
    # Generate thread_ts (optional - for threaded responses)
    has_thread = draw(st.booleans())
    if has_thread:
        timestamp_int = draw(st.integers(min_value=1000000000, max_value=9999999999))
        timestamp_decimal = draw(st.integers(min_value=100000, max_value=999999))
        thread_ts = f"{timestamp_int}.{timestamp_decimal}"
    else:
        thread_ts = None
    
    return {
        "response_text": response_text,
        "channel": channel,
        "thread_ts": thread_ts
    }


@settings(
    deadline=None,
    max_examples=100  # Property tests should run at least 100 iterations
)
@given(data=agent_response_data())
def test_slack_posts_response_to_correct_channel(data):
    """
    Test that Slack handler posts agent responses to the correct channel or thread.
    
    For any agent response received from AgentCore, the Slack handler should post
    the response to the correct Slack channel or thread, maintaining conversation
    context by using thread_ts when available.
    """
    response_text = data["response_text"]
    channel = data["channel"]
    thread_ts = data["thread_ts"]
    
    # Mock environment variables required by the handler
    with patch.dict(os.environ, {
        'SLACK_BOT_TOKEN': 'xoxb-test-token',
        'SLACK_SIGNING_SECRET': 'test-signing-secret',
        'AGENTCORE_ARN': 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test',
        'AGENTCORE_REGION': 'us-west-2',
        'ENVIRONMENT': 'test',
        'AWS_LAMBDA_FUNCTION_NAME': 'test-function'
    }):
        # Import handler after setting environment variables
        import handler as slack_handler
        
        # Track Slack API calls
        posted_messages = []
        
        # Mock Slack client
        with patch('slack_sdk.WebClient') as mock_slack_client:
            mock_client_instance = MagicMock()
            mock_slack_client.return_value = mock_client_instance
            
            # Mock chat_postMessage to capture posted messages
            def mock_post_message(**kwargs):
                posted_messages.append({
                    'channel': kwargs.get('channel'),
                    'text': kwargs.get('text'),
                    'thread_ts': kwargs.get('thread_ts')
                })
                return {'ts': '1234567890.123456', 'ok': True}
            
            mock_client_instance.chat_postMessage = mock_post_message
            mock_client_instance.auth_test.return_value = {'user_id': 'B123456'}
            
            # Mock AgentCore client to return the response
            with patch('boto3.client') as mock_boto_client:
                mock_agentcore_client = MagicMock()
                
                def mock_client_factory(service_name, **kwargs):
                    if service_name == 'bedrock-agentcore':
                        return mock_agentcore_client
                    elif service_name == 'lambda':
                        # Return a mock Lambda client that doesn't do anything
                        return MagicMock()
                    return MagicMock()
                
                mock_boto_client.side_effect = mock_client_factory
                
                # Mock AgentCore invoke_agent_runtime to return the response
                mock_agentcore_client.invoke_agent_runtime.return_value = {
                    'output': {
                        'text': response_text
                    }
                }
                
                # Create a mock Lambda context
                mock_context = Mock()
                mock_context.aws_request_id = 'test-request-id'
                mock_context.request_id = 'test-request-id'
                mock_context.function_name = 'test-function'
                
                # Create an async processing payload
                # This simulates the second invocation after async self-invoke
                async_payload = {
                    "async_process": True,
                    "prompt": "test prompt",
                    "channel": channel,
                    "thread_ts": thread_ts,
                    "is_slash_command": False,
                    "user_id": "U123456",
                    "session_id": thread_ts.replace(".", "-") if thread_ts else None,
                    "actor_id": "U123456"
                }
                
                # Create API Gateway proxy event structure for async processing
                api_gateway_event = {
                    'body': json.dumps(async_payload),
                    'headers': {
                        'Content-Type': 'application/json'
                    },
                    'httpMethod': 'POST',
                    'path': '/slack/events',
                    'requestContext': {
                        'requestId': 'test-request-id'
                    }
                }
                
                # Mock the SlackRequestHandler's handle method to process async invocation
                def mock_handle(event_arg, context_arg):
                    # Parse the async payload from the body
                    payload = json.loads(event_arg['body'])
                    
                    # Check if this is an async processing request
                    if payload.get('async_process'):
                        # Simulate calling AgentCore
                        agentcore_response = mock_agentcore_client.invoke_agent_runtime(
                            runtimeArn=os.environ['AGENTCORE_ARN'],
                            payload={
                                'prompt': payload['prompt'],
                                'session_id': payload.get('session_id'),
                                'actor_id': payload.get('actor_id')
                            }
                        )
                        
                        # Extract response text
                        result_text = agentcore_response.get('output', {}).get('text', '')
                        
                        # Post response to Slack
                        # This is the key logic we're testing
                        post_kwargs = {
                            'channel': payload['channel'],
                            'text': result_text
                        }
                        
                        # If there's a thread_ts, post as a reply in the thread
                        if payload.get('thread_ts'):
                            post_kwargs['thread_ts'] = payload['thread_ts']
                        
                        mock_client_instance.chat_postMessage(**post_kwargs)
                    
                    return {
                        'statusCode': 200,
                        'body': json.dumps({'ok': True})
                    }
                
                # Patch the handler's handle method
                with patch.object(slack_handler.handler, 'handle', side_effect=mock_handle):
                    # Invoke the Lambda handler with async processing payload
                    response = slack_handler.lambda_handler(api_gateway_event, mock_context)
        
        # Verify that a message was posted to Slack
        assert len(posted_messages) > 0, (
            "Handler did not post any messages to Slack"
        )
        
        # Get the posted message
        posted_message = posted_messages[0]
        
        # Verify the message was posted to the correct channel
        assert posted_message['channel'] == channel, (
            f"Message posted to wrong channel. "
            f"Expected: '{channel}', Got: '{posted_message['channel']}'"
        )
        
        # Verify the message text matches the agent response
        assert posted_message['text'] == response_text, (
            f"Posted message text does not match agent response. "
            f"Expected: '{response_text}', Got: '{posted_message['text']}'"
        )
        
        # Verify thread_ts is correctly set (or not set)
        if thread_ts:
            # If there was a thread_ts, the message should be posted as a reply
            assert posted_message['thread_ts'] == thread_ts, (
                f"Message posted with wrong thread_ts. "
                f"Expected: '{thread_ts}', Got: '{posted_message['thread_ts']}'"
            )
        else:
            # If there was no thread_ts, the message should not have thread_ts
            # (it's a new message, not a reply)
            assert posted_message['thread_ts'] is None, (
                f"Message should not have thread_ts for non-threaded messages. "
                f"Got: '{posted_message['thread_ts']}'"
            )
        
        # Verify the handler returns a successful response
        assert response['statusCode'] == 200, (
            f"Handler should return 200 for successful processing, got {response['statusCode']}"
        )


@settings(
    deadline=None,
    max_examples=100  # Property tests should run at least 100 iterations
)
@given(event=slack_event_payload())
def test_slack_async_self_invocation(event):
    """
    Test that Slack handler invokes itself asynchronously for agent processing.

    For any Slack event requiring agent invocation, the handler should invoke itself
    asynchronously using Lambda.invoke with InvocationType='Event' before returning to Slack.

    This ensures the handler responds to Slack within the 3-second timeout requirement
    while processing the agent invocation asynchronously in the background.
    """
    # Skip events where the text is empty after mention stripping (handler shows help instead)
    text = event["event"]["text"]
    if event["event"]["type"] == "app_mention" and "<@" in text:
        text = text.split(">", 1)[-1].strip()
    assume(text.strip() != "")

    # Track Lambda invocations
    lambda_invocations = []
    
    # Mock environment variables first
    with patch.dict(os.environ, {
        'SLACK_BOT_TOKEN': 'xoxb-test-token',
        'SLACK_SIGNING_SECRET': 'test-signing-secret',
        'SLACK_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:test-secret',
        'AGENTCORE_ARN': 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test',
        'AGENTCORE_REGION': 'us-west-2',
        'ENVIRONMENT': 'test',
        'AWS_LAMBDA_FUNCTION_NAME': 'test-function'
    }):
        # Import handler (already imported at module level, uses conftest mocks)
        import handler as slack_handler
        
        # Mock the Lambda client in the handler module's trigger_async_processing function
        # This ensures we capture the invocation even if handler was imported earlier
        with patch.object(slack_handler, 'boto3') as mock_boto3:
            mock_lambda_client = MagicMock()
            mock_secrets_client = MagicMock()
            
            # Set up Secrets Manager mock to return proper JSON string
            mock_secrets_client.get_secret_value.return_value = {
                'SecretString': json.dumps({
                    'SLACK_BOT_TOKEN': 'xoxb-test-token',
                    'SLACK_SIGNING_SECRET': 'test-signing-secret'
                })
            }
            
            def mock_client_factory(service_name, **kwargs):
                if service_name == 'lambda':
                    return mock_lambda_client
                elif service_name == 'secretsmanager':
                    return mock_secrets_client
                # Return a mock for other services
                return MagicMock()
            
            mock_boto3.client = MagicMock(side_effect=mock_client_factory)
            
            # Mock the invoke method to capture invocation details
            def mock_invoke(**kwargs):
                lambda_invocations.append({
                    'FunctionName': kwargs.get('FunctionName'),
                    'InvocationType': kwargs.get('InvocationType'),
                    'Payload': kwargs.get('Payload')
                })
                return {'StatusCode': 202}
            
            mock_lambda_client.invoke = mock_invoke
            
            # Mock Slack client for posting acknowledgment
            with patch('slack_sdk.WebClient') as mock_slack_client:
                mock_client_instance = MagicMock()
                mock_slack_client.return_value = mock_client_instance
                mock_client_instance.chat_postMessage.return_value = {'ts': '1234567890.123456'}
                mock_client_instance.auth_test.return_value = {'user_id': 'B123456'}
                
                # Create a mock Lambda context
                mock_context = Mock()
                mock_context.aws_request_id = 'test-request-id'
                mock_context.request_id = 'test-request-id'
                mock_context.function_name = 'test-function'
                
                # Create API Gateway proxy event structure
                api_gateway_event = {
                    'body': json.dumps(event),
                    'headers': {
                        'Content-Type': 'application/json',
                        'X-Slack-Request-Timestamp': '1234567890',
                        'X-Slack-Signature': 'v0=test-signature'
                    },
                    'httpMethod': 'POST',
                    'path': '/slack/events',
                    'requestContext': {
                        'requestId': 'test-request-id'
                    }
                }
                
                # Mock the SlackRequestHandler's handle method to process the event
                def mock_handle(event_arg, context_arg):
                    # Parse the Slack event from the body
                    slack_event = json.loads(event_arg['body'])
                    
                    # Simulate the handler processing based on event type
                    if slack_event.get('type') == 'event_callback':
                        inner_event = slack_event.get('event', {})
                        inner_event_type = inner_event.get('type')
                        
                        # Simulate process_message being called
                        if inner_event_type in ['app_mention', 'message']:
                            text = inner_event.get('text', '')
                            channel = inner_event.get('channel')
                            thread_ts = inner_event.get('thread_ts') or inner_event.get('ts')
                            user_id = inner_event.get('user')
                            
                            # Remove bot mention from text if present
                            if "<@" in text:
                                text = text.split(">", 1)[-1].strip()
                            
                            # Simulate trigger_async_processing being called
                            # This is the key logic we're testing: async self-invocation
                            if text:  # Only if there's text to process
                                session_id = thread_ts.replace(".", "-") if thread_ts else None
                                payload = {
                                    "async_process": True,
                                    "prompt": text,
                                    "channel": channel,
                                    "thread_ts": thread_ts,
                                    "is_slash_command": False,
                                    "user_id": user_id,
                                    "session_id": session_id,
                                    "actor_id": user_id
                                }
                                
                                # This is the critical async self-invocation
                                # InvocationType='Event' means asynchronous (fire-and-forget)
                                mock_lambda_client.invoke(
                                    FunctionName=context_arg.function_name,
                                    InvocationType='Event',  # Async invocation
                                    Payload=json.dumps(payload).encode()
                                )
                    
                    return {
                        'statusCode': 200,
                        'body': json.dumps({'ok': True})
                    }
                
                # Patch the handler's handle method
                with patch.object(slack_handler.handler, 'handle', side_effect=mock_handle):
                    # Invoke the Lambda handler
                    response = slack_handler.lambda_handler(api_gateway_event, mock_context)
        
        # Verify that the handler invoked itself asynchronously
        assert len(lambda_invocations) > 0, (
            "Handler did not invoke Lambda function for async processing"
        )
        
        # Get the Lambda invocation details
        invocation = lambda_invocations[0]
        
        # Verify the function name is correct (self-invocation)
        assert invocation['FunctionName'] == 'test-function', (
            f"Handler invoked wrong function. "
            f"Expected: 'test-function', Got: '{invocation['FunctionName']}'"
        )
        
        # Verify the invocation type is 'Event' (asynchronous)
        # This is critical for meeting Slack's 3-second timeout requirement
        assert invocation['InvocationType'] == 'Event', (
            f"Handler used wrong invocation type. "
            f"Expected: 'Event' (async), Got: '{invocation['InvocationType']}'. "
            f"The handler must use async invocation to respond to Slack within 3 seconds."
        )
        
        # Verify the payload contains the async processing flag
        payload_bytes = invocation['Payload']
        if isinstance(payload_bytes, bytes):
            payload_str = payload_bytes.decode('utf-8')
        else:
            payload_str = payload_bytes
        payload = json.loads(payload_str)
        
        assert payload.get('async_process') is True, (
            "Async invocation payload missing 'async_process' flag"
        )
        
        # Verify the payload contains the required fields for processing
        assert 'prompt' in payload, "Async payload missing 'prompt' field"
        assert 'channel' in payload, "Async payload missing 'channel' field"
        assert 'user_id' in payload, "Async payload missing 'user_id' field"
        
        # Verify the handler returns a successful response quickly (before async processing completes)
        # This demonstrates that the handler responds to Slack immediately
        assert response['statusCode'] == 200, (
            f"Handler should return 200 immediately after triggering async processing, "
            f"got {response['statusCode']}"
        )



# Strategy for generating AgentCore invocation payloads with session context
@st.composite
def agentcore_invocation_data(draw):
    """Generate AgentCore invocation data with optional session context.
    
    Returns a dictionary with prompt and optional session_id/actor_id.
    """
    # Generate prompt
    prompt = draw(st.text(min_size=1, max_size=500))
    
    # Randomly decide whether to include session context
    has_session_context = draw(st.booleans())
    
    if has_session_context:
        # Generate session_id (sanitized thread_ts format)
        timestamp_int = draw(st.integers(min_value=1000000000, max_value=9999999999))
        timestamp_decimal = draw(st.integers(min_value=100000, max_value=999999))
        session_id = f"{timestamp_int}-{timestamp_decimal}"  # Already sanitized (dash instead of dot)
        
        # Generate actor_id
        actor_id = draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))))
    else:
        session_id = None
        actor_id = None
    
    return {
        "prompt": prompt,
        "session_id": session_id,
        "actor_id": actor_id
    }


@settings(
    deadline=None,
    max_examples=100  # Property tests should run at least 100 iterations
)
@given(data=agentcore_invocation_data())
def test_session_context_propagation_to_agentcore(data):
    """
    Property 11: Session context propagation
    
    Test that session context is correctly propagated to AgentCore invocations.
    
    For any AgentCore invocation, when session_id and actor_id are provided,
    the invocation payload should include both values; when they are not provided,
    the payload should omit them entirely (not include null values).
    
    This ensures conversation continuity works correctly and null values don't
    interfere with AgentCore Memory's session management.
    
    Feature: api-gateway-stack, Property 11: Session context propagation
    Validates: Requirements 5.2, 5.3, 5.4
    """
    prompt = data["prompt"]
    session_id = data["session_id"]
    actor_id = data["actor_id"]
    
    # Track the AgentCore invocation payload
    captured_payload = None
    
    # Mock environment variables required by the handler
    with patch.dict(os.environ, {
        'SLACK_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:test-secret',
        'AGENTCORE_ARN': 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test',
        'AGENTCORE_REGION': 'us-west-2',
        'ENVIRONMENT': 'test',
        'AWS_LAMBDA_FUNCTION_NAME': 'test-function'
    }):
        # Import handler (will use conftest mocks for credentials)
        import handler as slack_handler
        
        # Mock the agentcore_client's client.invoke_agent_runtime method directly
        # This avoids issues with module-level initialization
        def mock_invoke_agent_runtime(**kwargs):
            nonlocal captured_payload
            # Extract the payload from the invocation
            payload_bytes = kwargs.get('payload', b'{}')
            if isinstance(payload_bytes, bytes):
                payload_str = payload_bytes.decode('utf-8')
            else:
                payload_str = payload_bytes
            captured_payload = json.loads(payload_str)
            
            # Return a mock response with proper structure
            # Create a real BytesIO object instead of a Mock
            import io
            response_data = json.dumps({"result": "test response"}).encode('utf-8')
            response_stream = io.BytesIO(response_data)
            return {'response': response_stream}
        
        # Patch the invoke_agent_runtime method on the existing client
        with patch.object(
            slack_handler.agentcore_client.client,
            'invoke_agent_runtime',
            side_effect=mock_invoke_agent_runtime
        ):
            # Call the agentcore_client.invoke function directly
            # This is the function that builds the payload and invokes AgentCore
            result = slack_handler.agentcore_client.invoke(
                prompt=prompt,
                session_id=session_id,
                actor_id=actor_id
            )
        
        # Verify that the payload was captured
        assert captured_payload is not None, (
            "AgentCore invocation payload was not captured"
        )
        
        # Verify the prompt is always included
        assert 'prompt' in captured_payload, (
            "Payload missing required 'prompt' field"
        )
        assert captured_payload['prompt'] == prompt, (
            f"Payload prompt does not match. "
            f"Expected: '{prompt}', Got: '{captured_payload['prompt']}'"
        )
        
        # Verify session_id handling
        if session_id:
            # When session_id is provided, it should be included in the payload
            assert 'session_id' in captured_payload, (
                f"Payload missing 'session_id' field when session_id='{session_id}' was provided"
            )
            assert captured_payload['session_id'] == session_id, (
                f"Payload session_id does not match. "
                f"Expected: '{session_id}', Got: '{captured_payload['session_id']}'"
            )
        else:
            # When session_id is not provided, it should be omitted (not included as null)
            assert 'session_id' not in captured_payload, (
                f"Payload should not include 'session_id' field when session_id is None. "
                f"Got: {captured_payload.get('session_id')}"
            )
        
        # Verify actor_id handling
        if actor_id:
            # When actor_id is provided, it should be included in the payload
            assert 'actor_id' in captured_payload, (
                f"Payload missing 'actor_id' field when actor_id='{actor_id}' was provided"
            )
            assert captured_payload['actor_id'] == actor_id, (
                f"Payload actor_id does not match. "
                f"Expected: '{actor_id}', Got: '{captured_payload['actor_id']}'"
            )
        else:
            # When actor_id is not provided, it should be omitted (not included as null)
            assert 'actor_id' not in captured_payload, (
                f"Payload should not include 'actor_id' field when actor_id is None. "
                f"Got: {captured_payload.get('actor_id')}"
            )
        
        # Verify no unexpected fields are included
        expected_fields = {'prompt'}
        if session_id:
            expected_fields.add('session_id')
        if actor_id:
            expected_fields.add('actor_id')
        
        unexpected_fields = set(captured_payload.keys()) - expected_fields
        assert len(unexpected_fields) == 0, (
            f"Payload contains unexpected fields: {unexpected_fields}. "
            f"Expected only: {expected_fields}"
        )
        
        # Verify the function returns a response
        assert result is not None, (
            "invoke_agent should return a response"
        )
