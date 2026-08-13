"""
Test Lambda Handler for Slack Integration Testing

This handler completely bypasses the Slack Bolt SDK and directly processes events
using mock Slack clients to record API calls instead of making them.
It returns the recorded calls in the response for integration test verification.

This handler is ONLY deployed in test/dev environments for integration testing.
"""

import json
import os
import logging

# Import shared core logic
from core.utils import setup_structured_logging, sanitize_error_message
from core.event_processor import (
    extract_message_data,
    extract_slash_command_data,
    clean_mention_from_text,
    should_skip_message,
    is_direct_message,
    is_thread_reply,
    has_mention
)
from core.session_manager import sanitize_session_id
from core.agentcore_client import AgentCoreClient

# Import mock Slack adapter
from adapters.slack_mock import MockSlackClient

# Setup structured logging
setup_structured_logging()
logger = logging.getLogger()

# Initialize mock Slack client
mock_slack_client = MockSlackClient()

# AgentCore configuration
AGENTCORE_ARN = os.environ.get("AGENTCORE_ARN")
if not AGENTCORE_ARN:
    raise ValueError("AGENTCORE_ARN environment variable is required")

AGENTCORE_REGION = os.environ.get("AGENTCORE_REGION") or AGENTCORE_ARN.split(":")[3]
ENVIRONMENT = os.environ.get("ENVIRONMENT", "test")

# Initialize AgentCore client
agentcore_client = AgentCoreClient(AGENTCORE_ARN, AGENTCORE_REGION)


def invoke_agent_with_recording(
    prompt: str,
    session_id: str = None,
    actor_id: str = None
) -> dict:
    """Invoke AgentCore and record the invocation details.
    
    Returns both the response and invocation metadata for test verification.
    
    Args:
        prompt: The user's question
        session_id: Optional session ID
        actor_id: Optional actor ID
        
    Returns:
        Dictionary with response and invocation metadata
    """
    try:
        response = agentcore_client.invoke(prompt, session_id, actor_id)
        
        return {
            "success": True,
            "response": response,
            "invocation": {
                "prompt": prompt,
                "session_id": session_id,
                "actor_id": actor_id,
                "runtime_arn": AGENTCORE_ARN
            }
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error invoking AgentCore: {error_msg}", extra={'error': error_msg})
        
        return {
            "success": False,
            "error": sanitize_error_message(error_msg),
            "invocation": {
                "prompt": prompt,
                "session_id": session_id,
                "actor_id": actor_id,
                "runtime_arn": AGENTCORE_ARN
            }
        }


def process_async_test(payload: dict) -> dict:
    """Process async request and return recorded interactions.
    
    Args:
        payload: Request payload with prompt, channel, etc.
        
    Returns:
        Dictionary with recorded Slack calls and AgentCore invocation
    """
    import time
    start_time = time.time()
    
    # Clear previous recordings
    mock_slack_client.clear_recorded_calls()
    
    prompt = payload.get("prompt", "")
    channel = payload.get("channel")
    thread_ts = payload.get("thread_ts")
    is_slash_command = payload.get("is_slash_command", False)
    session_id = payload.get("session_id")
    actor_id = payload.get("actor_id")
    user_id = payload.get("user_id")
    
    logger.info(
        "Processing test async request",
        extra={
            'session_id': session_id,
            'actor_id': actor_id,
            'is_slash_command': is_slash_command
        }
    )
    
    # Simulate the production flow
    if is_slash_command:
        # For slash commands: post question, then answer in thread
        header = f"<@{user_id}> asked: _{prompt}_"
        parent_ts = mock_slack_client.post_message(channel, header)
        
        # Use parent ts as session_id
        session_id = sanitize_session_id(parent_ts)
        
        # Invoke agent
        agent_result = invoke_agent_with_recording(prompt, session_id, actor_id)
        
        # Post response in thread
        response_text = agent_result.get("response", agent_result.get("error", "Error"))
        mock_slack_client.post_message(channel, response_text, thread_ts=parent_ts)
    else:
        # For mentions/DMs: reply in existing thread
        agent_result = invoke_agent_with_recording(prompt, session_id, actor_id)
        response_text = agent_result.get("response", agent_result.get("error", "Error"))
        mock_slack_client.post_message(channel, response_text, thread_ts)
    
    duration_ms = int((time.time() - start_time) * 1000)
    
    # Return recorded interactions
    return {
        "test_mode": True,
        "duration_ms": duration_ms,
        "slack_calls": mock_slack_client.get_recorded_calls(),
        "agentcore_invocation": agent_result.get("invocation"),
        "agentcore_success": agent_result.get("success"),
        "agentcore_response": agent_result.get("response"),
        "agentcore_error": agent_result.get("error"),
        "session_context": {
            "session_id": session_id,
            "actor_id": actor_id
        }
    }


def trigger_async_processing_test(
    prompt: str,
    channel: str,
    thread_ts: str,
    user_id: str = None,
    is_slash_command: bool = False
) -> dict:
    """Trigger async processing in test mode (synchronous for testing).
    
    Instead of async Lambda invocation, processes synchronously and returns results.
    
    Args:
        prompt: The user's question
        channel: Slack channel ID
        thread_ts: Thread timestamp
        user_id: Slack user ID
        is_slash_command: Whether from slash command
        
    Returns:
        Dictionary with recorded interactions
    """
    session_id = sanitize_session_id(thread_ts) if thread_ts else None
    
    payload = {
        "async_process": True,
        "prompt": prompt,
        "channel": channel,
        "thread_ts": thread_ts,
        "is_slash_command": is_slash_command,
        "user_id": user_id,
        "session_id": session_id,
        "actor_id": user_id
    }
    
    return process_async_test(payload)


def process_message_test(body: dict) -> dict:
    """Process message in test mode and return recorded interactions.
    
    Args:
        body: Slack event body (already parsed as dict)
        
    Returns:
        Dictionary with recorded interactions
    """
    event_data = extract_message_data(body)
    text = clean_mention_from_text(event_data["text"])
    
    if not text:
        # Record the "help" message
        mock_slack_client.post_message(
            event_data["channel"],
            "Hi! Ask me about Bedrock quotas, model metrics, or utilization.",
            thread_ts=event_data["thread_ts"]
        )
        return {
            "test_mode": True,
            "message_type": "help",
            "slack_calls": mock_slack_client.get_recorded_calls()
        }
    
    # Record acknowledgment
    mock_slack_client.post_message(
        event_data["channel"],
        ":hourglass_flowing_sand: Checking with the Bedrock Quota Agent...",
        thread_ts=event_data["thread_ts"]
    )
    
    # Process async and get results
    result = trigger_async_processing_test(
        text,
        event_data["channel"],
        event_data["thread_ts"],
        event_data["user_id"]
    )
    
    return result


def lambda_handler(event, context):
    """Test Lambda entry point.
    
    Returns recorded interactions for integration test verification.
    
    Args:
        event: Lambda event
        context: Lambda context
        
    Returns:
        Response with recorded Slack calls and AgentCore invocations
    """
    import time
    start_time = time.time()
    request_id = context.aws_request_id if context else 'unknown'
    
    try:
        logger.info("Test handler received event", extra={'request_id': request_id})
        
        # Clear previous recordings
        mock_slack_client.clear_recorded_calls()
        
        # Check if this is a direct async test request
        if event.get("async_process"):
            result = process_async_test(event)
            return {
                "statusCode": 200,
                "body": json.dumps(result)
            }
        
        # Parse the body if it's a string (from API Gateway)
        if isinstance(event.get("body"), str):
            try:
                body = json.loads(event["body"])
            except json.JSONDecodeError:
                body = event
        else:
            body = event.get("body", event)
        
        # Handle URL verification challenge
        if body.get("type") == "url_verification":
            return {
                "statusCode": 200,
                "body": json.dumps({"challenge": body.get("challenge")})
            }
        
        # Handle event_callback (app_mention, message, etc.)
        if body.get("type") == "event_callback":
            event_type = body.get("event", {}).get("type")
            
            if event_type == "app_mention":
                result = process_message_test(body)
            elif event_type == "message":
                event_data = extract_message_data(body)
                
                if should_skip_message(event_data):
                    return {"statusCode": 200, "body": json.dumps({"ok": True})}
                
                if is_direct_message(event_data):
                    result = process_message_test(body)
                elif is_thread_reply(event_data) and not has_mention(event_data["text"]):
                    # Check thread parent
                    try:
                        bot_user_id = mock_slack_client.get_bot_user_id()
                        parent = mock_slack_client.get_thread_parent(
                            event_data["channel"],
                            event_data["thread_ts"]
                        )
                        
                        parent_by_bot = parent.get("user") == bot_user_id
                        bot_mentioned = f"<@{bot_user_id}>" in parent.get("text", "")
                        
                        if parent_by_bot or bot_mentioned:
                            result = process_message_test(body)
                        else:
                            return {"statusCode": 200, "body": json.dumps({"ok": True})}
                    except Exception as e:
                        logger.error(f"Error checking thread parent: {str(e)}")
                        return {"statusCode": 200, "body": json.dumps({"ok": True})}
                else:
                    return {"statusCode": 200, "body": json.dumps({"ok": True})}
            else:
                return {"statusCode": 200, "body": json.dumps({"ok": True})}
            
            duration_ms = int((time.time() - start_time) * 1000)
            result["duration_ms"] = duration_ms
            result["request_id"] = request_id
            
            return {
                "statusCode": 200,
                "body": json.dumps(result)
            }
        
        # Handle slash command
        if body.get("command"):
            cmd_data = extract_slash_command_data(body)
            
            if not cmd_data["text"]:
                result = {
                    "test_mode": True,
                    "message_type": "usage",
                    "slack_calls": [{
                        "method": "respond",
                        "text": "Usage: `/bedrock <your question>`"
                    }]
                }
            else:
                result = trigger_async_processing_test(
                    cmd_data["text"],
                    cmd_data["channel"],
                    None,
                    cmd_data["user_id"],
                    is_slash_command=True
                )
            
            duration_ms = int((time.time() - start_time) * 1000)
            result["duration_ms"] = duration_ms
            result["request_id"] = request_id
            
            return {
                "statusCode": 200,
                "body": json.dumps(result)
            }
        
        # Unknown event type
        return {
            "statusCode": 200,
            "body": json.dumps({"ok": True})
        }
        
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)
        logger.error(
            f"Error in test handler: {error_msg}",
            extra={'error': error_msg, 'request_id': request_id, 'duration_ms': duration_ms}
        )
        
        return {
            "statusCode": 500,
            "body": json.dumps({
                "test_mode": True,
                "error": sanitize_error_message(error_msg),
                "request_id": request_id,
                "duration_ms": duration_ms
            })
        }
