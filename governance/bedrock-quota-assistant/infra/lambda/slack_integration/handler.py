"""
Slack Bot Lambda Handler for Bedrock Quota Agent

This Lambda function handles Slack events and forwards queries to the
AgentCore runtime. Uses async invocation pattern to handle Slack's
3-second timeout requirement.
"""

import json
import os
import boto3
from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

# Import shared core modules
from core.event_processor import SlackEventProcessor
from core.session_manager import SessionManager
from core.agentcore_client import AgentCoreClient
from core.utils import setup_structured_logging, sanitize_error_message
from core.secrets_manager import get_slack_credentials, SlackCredentialsError
from adapters.slack_real import RealSlackClient

# Configure structured logging
logger = setup_structured_logging()

# Retrieve Slack credentials from Secrets Manager
# For unit tests, mock get_slack_credentials before importing this module
try:
    credentials = get_slack_credentials()
    slack_bot_token = credentials["SLACK_BOT_TOKEN"]
    slack_signing_secret = credentials["SLACK_SIGNING_SECRET"]
    logger.info("Slack credentials retrieved successfully from Secrets Manager")
except SlackCredentialsError as e:
    logger.warning(f"Failed to retrieve Slack credentials: {e}")
    raise

# Initialize Slack app with bot token and signing secret from Secrets Manager
app = App(
    token=slack_bot_token,
    signing_secret=slack_signing_secret,
    process_before_response=True  # Required for Lambda
)

# AgentCore configuration
AGENTCORE_ARN = os.environ.get("AGENTCORE_ARN")
if not AGENTCORE_ARN:
    raise ValueError(
        "AGENTCORE_ARN environment variable is required.\n"
        "Set it to your AgentCore runtime ARN, e.g.:\n"
        "  arn:aws:bedrock-agentcore:us-west-2:<account-id>:runtime/<runtime-id>"
    )
AGENTCORE_REGION = os.environ.get("AGENTCORE_REGION") or AGENTCORE_ARN.split(":")[3]
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

# Lambda function name for async invocation
FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")

# Slash command configuration
SLASH_COMMAND = os.environ.get("SLACK_SLASH_COMMAND", "/bedrock")

# Dedup configuration - reuse cache table to prevent duplicate event processing.
#
# This is a cost control, not just a correctness nicety. Slack retries a webhook
# whenever it does not get a 2xx, so without deduplication a single user message
# can become several Amazon Bedrock invocations. Refuse to start rather than run
# with deduplication silently disabled.
DEDUP_TABLE_NAME = os.environ.get("DEDUP_TABLE_NAME")
if not DEDUP_TABLE_NAME:
    raise ValueError(
        "DEDUP_TABLE_NAME environment variable is required.\n"
        "Event deduplication bounds the number of Amazon Bedrock invocations a "
        "retried Slack webhook can trigger. Running without it removes that "
        "bound, so startup is refused.\n"
        "Set it to the DynamoDB table used for deduplication."
    )
DEDUP_TTL_SECONDS = 60

# Initialize shared components
agentcore_client = AgentCoreClient(AGENTCORE_ARN, AGENTCORE_REGION)
slack_client = RealSlackClient(slack_bot_token)
event_processor = SlackEventProcessor()
session_manager = SessionManager()


def _is_duplicate_event(message_ts: str) -> bool:
    """Check if this message has already been processed using DynamoDB conditional put.

    Args:
        message_ts: Slack message timestamp (unique per message)

    Returns:
        True if the event must not be processed (already seen, or the dedup check
        could not be completed), False if this is the first time we have seen it.

    Fails closed. If DynamoDB is unavailable or throttled we cannot tell a first
    delivery from a retry, so we treat the event as a duplicate and drop it. That
    trades availability for cost: the failure mode is a Slack message that goes
    unanswered rather than an unbounded multiplier on Amazon Bedrock invocations.
    The old behaviour returned False here, which meant the control disappeared
    exactly under the load it existed to contain — DynamoDB throttling plus Slack
    webhook retries would amplify invocations instead of suppressing them.
    """
    if not message_ts:
        # No timestamp means no dedup key. Callers derive this from the Slack
        # event, so an absent value indicates a payload we do not understand.
        logger.error("Dedup check received no message_ts; dropping event")
        return True

    import time
    from botocore.exceptions import ClientError

    try:
        # Client construction is inside the try on purpose: a missing region or
        # credential resolution failure raises here, not at put_item, and must
        # fail closed like any other dedup failure.
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(DEDUP_TABLE_NAME)

        table.put_item(
            Item={
                "PK": f"dedup#{message_ts}",
                "SK": "event",
                "ttl": int(time.time()) + DEDUP_TTL_SECONDS,
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
        return False  # First time seeing this event
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.warning(
                "Duplicate event detected, skipping",
                extra={"message_ts": message_ts},
            )
            return True
        # Unexpected AWS error (throttling, permissions) — fail closed.
        # We cannot distinguish a first delivery from a Slack retry, so drop the
        # event rather than risk multiplying Amazon Bedrock invocations.
        logger.error(
            f"Dedup check failed, dropping event to bound cost: {e}",
            extra={"message_ts": message_ts},
        )
        return True
    except Exception as e:
        # Connection timeouts and endpoint errors are BotoCoreError, not
        # ClientError, and would otherwise escape this function. Catch them here
        # so the fail-closed guarantee holds for every failure mode, not just the
        # ones DynamoDB reports as API errors.
        logger.error(
            f"Dedup check raised unexpectedly, dropping event to bound cost: {e}",
            extra={"message_ts": message_ts},
        )
        return True


def _post_with_streaming(channel: str, text: str, thread_ts: str):
    """Post a response using text streaming (startStream/appendStream/stopStream).

    Falls back to a regular post_message if the streaming APIs are unavailable
    (e.g., workspace doesn't have Agents & AI Apps enabled).

    Args:
        channel: Slack channel ID
        text: Full response text from AgentCore
        thread_ts: Thread timestamp for the reply
    """
    from core.markdown_to_slack import markdown_to_slack

    slack_text = markdown_to_slack(text)

    try:
        # Start the stream
        start_resp = slack_client.client.api_call(
            "chat.startStream",
            json={
                "channel": channel,
                "thread_ts": thread_ts,
                "chunks": [{"type": "markdown_text", "markdown_text": ""}],
            },
        )
        if not start_resp.get("ok"):
            raise Exception(start_resp.get("error", "startStream failed"))

        message_ts = start_resp["ts"]

        # Stream the response in chunks (~500 char segments for smooth rendering)
        CHUNK_SIZE = 500
        for i in range(0, len(slack_text), CHUNK_SIZE):
            chunk = slack_text[i : i + CHUNK_SIZE]
            slack_client.client.api_call(
                "chat.appendStream",
                json={
                    "channel": channel,
                    "message_ts": message_ts,
                    "thread_ts": thread_ts,
                    "chunks": [{"type": "markdown_text", "markdown_text": chunk}],
                },
            )

        # Stop the stream
        slack_client.client.api_call(
            "chat.stopStream",
            json={
                "channel": channel,
                "message_ts": message_ts,
                "thread_ts": thread_ts,
            },
        )

    except Exception as e:
        logger.warning(f"Streaming failed, falling back to post_message: {e}")
        slack_client.post_message(channel, text, thread_ts)


def process_async(payload: dict):
    """Process the agent request and post response to Slack (called async).
    
    Args:
        payload: Dictionary containing prompt, channel, thread_ts, and session context
    """
    import time
    start_time = time.time()
    
    try:
        prompt = payload.get("prompt", "")
        channel = payload.get("channel")
        thread_ts = payload.get("thread_ts")
        is_slash_command = payload.get("is_slash_command", False)
        session_id = payload.get("session_id")
        actor_id = payload.get("actor_id")
        user_id = payload.get("user_id")
        
        logger.info(
            "Starting async processing",
            extra={
                'session_id': session_id,
                'actor_id': actor_id,
                'is_slash_command': is_slash_command
            }
        )
        
        if is_slash_command:
            # For slash commands: create a thread by posting the question first,
            # then reply with the answer. This establishes thread_ts for follow-ups.
            header = f"<@{user_id}> asked: _{prompt}_"
            parent_ts = slack_client.post_message(channel, header)
            
            # Use the parent message ts as session_id for this conversation
            session_id = session_manager.sanitize_session_id(parent_ts) if parent_ts else None
            
            response = agentcore_client.invoke(prompt, session_id=session_id, actor_id=actor_id)
            # Reply in the thread we just created
            _post_with_streaming(channel, response, parent_ts)
        else:
            # For @mentions, DMs, thread replies: reply in the existing thread
            response = agentcore_client.invoke(prompt, session_id=session_id, actor_id=actor_id)
            _post_with_streaming(channel, response, thread_ts)
        
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "Async processing completed successfully",
            extra={
                'session_id': session_id,
                'actor_id': actor_id,
                'duration_ms': duration_ms
            }
        )
            
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)
        logger.error(
            f"Error in async processing: {error_msg}",
            extra={
                'error': error_msg,
                'session_id': payload.get('session_id'),
                'actor_id': payload.get('actor_id'),
                'duration_ms': duration_ms
            }
        )
        # Attempt to notify user of error with sanitized message
        try:
            sanitized_msg = sanitize_error_message(error_msg)
            error_msg = f"Sorry, I encountered an error processing your request: {sanitized_msg}"
            slack_client.post_message(channel, error_msg, thread_ts)
        except Exception:
            logger.error("Failed to post error message to Slack")
            pass  # If we can't post error, just log it



def trigger_async_processing(
    prompt: str,
    channel: str,
    thread_ts: str,
    user_id: str = None,
    is_slash_command: bool = False
):
    """Trigger async Lambda invocation to process the request.
    
    Args:
        prompt: The user's question
        channel: Slack channel ID
        thread_ts: Thread timestamp for replies
        user_id: Slack user ID
        is_slash_command: Whether this came from a slash command
    """
    try:
        lambda_client = boto3.client('lambda')
        
        # Sanitize session_id using session manager
        session_id = session_manager.sanitize_session_id(thread_ts) if thread_ts else None
        
        payload = {
            "async_process": True,
            "prompt": prompt,
            "channel": channel,
            "thread_ts": thread_ts,
            "is_slash_command": is_slash_command,
            "user_id": user_id,
            # Session context for multi-turn conversations
            # For slash commands, session_id is None here but gets set in process_async
            # from the posted message's timestamp to enable thread-based follow-ups
            "session_id": session_id,
            "actor_id": user_id  # Slack user = actor (enables per-user LTM)
        }
        
        lambda_client.invoke(
            FunctionName=FUNCTION_NAME,
            InvocationType='Event',  # Async invocation
            Payload=json.dumps(payload).encode()
        )
        
        logger.info(
            "Triggered async processing",
            extra={'session_id': session_id, 'actor_id': user_id}
        )
        
    except Exception as e:
        error_msg = str(e)
        logger.warning(
            f"Error triggering async processing: {error_msg}",
            extra={'error': error_msg}
        )
        # Re-raise with sanitized message
        sanitized_msg = sanitize_error_message(error_msg)
        raise Exception(f"Failed to trigger async processing: {sanitized_msg}")



def process_message(body: dict, say, client):
    """Process incoming message - acknowledge immediately, process async.
    
    Args:
        body: Slack event body
        say: Slack say function for quick responses
        client: Slack WebClient
    """
    try:
        # Parse event using event processor
        event_data = event_processor.parse_event(body)
        
        logger.info(
            "Processing message",
            extra={'channel': event_data['channel'], 'user_id': event_data['user_id']}
        )
        
        # Dedup check — skip if another Lambda instance already processed this event
        message_ts = event_data.get('ts') or event_data.get('thread_ts')
        if _is_duplicate_event(message_ts):
            return
        
        if not event_data['text']:
            say(
                "Hi! Ask me about Bedrock quotas, model metrics, or utilization.",
                thread_ts=event_data['thread_ts']
            )
            return
        
        # Set loading status via Agents & AI Apps API
        try:
            client.assistant_threads_setStatus(
                channel_id=event_data['channel'],
                thread_ts=event_data['thread_ts'],
                status="Analyzing your Bedrock quotas and metrics..."
            )
        except Exception as e:
            logger.warning(
                f"Failed to set assistant status (falling back to message): {e}",
                extra={'error': str(e)}
            )
            # Fallback for workspaces without Agents & AI Apps enabled
            try:
                client.chat_postMessage(
                    channel=event_data['channel'],
                    thread_ts=event_data['thread_ts'],
                    text=":hourglass_flowing_sand: Checking with the Bedrock Quota Agent..."
                )
            except Exception:
                pass
        
        # Trigger async processing with session context
        trigger_async_processing(
            event_data['text'],
            event_data['channel'],
            event_data['thread_ts'],
            event_data['user_id']
        )
        
    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"Error processing message: {error_msg}",
            extra={'error': error_msg}
        )
        # Return sanitized error to user
        try:
            sanitized_msg = sanitize_error_message(error_msg)
            say(f"Sorry, I encountered an error: {sanitized_msg}", thread_ts=event_data.get('thread_ts'))
        except Exception:
            pass



# Handle app mentions (@bot) - starts a conversation
@app.event("app_mention")
def handle_mention(body, say, client):
    """Handle app mention events."""
    process_message(body, say, client)


# Cache bot user ID to avoid repeated auth_test calls
_bot_user_id = None

def get_bot_user_id(client):
    """Get and cache the bot's user ID.
    
    Args:
        client: Slack WebClient
        
    Returns:
        The bot's user ID
    """
    global _bot_user_id
    if _bot_user_id is None:
        _bot_user_id = client.auth_test()["user_id"]
    return _bot_user_id


# Handle messages - for DMs and thread replies
@app.event("message")
def handle_message(body, say, client):
    """Handle message events.
    
    Args:
        body: Slack event body
        say: Slack say function
        client: Slack WebClient
    """
    try:
        event = body.get("event", {})
        
        # Skip bot messages and message subtypes (edits, deletes, etc.)
        if event.get("bot_id") or event.get("subtype"):
            return
        
        channel_type = event.get("channel_type")
        thread_ts = event.get("thread_ts")
        ts = event.get("ts")
        text = event.get("text", "")
        channel = event.get("channel")
        
        # Handle DMs - always respond
        if channel_type == "im":
            process_message(body, say, client)
            return
        
        # For channel messages, only respond to thread replies (not top-level messages)
        # Top-level messages require @mention (handled by app_mention)
        if not thread_ts or thread_ts == ts:
            return
        
        # Skip if user @mentions anyone (app_mention handler will catch if it's us)
        if "<@" in text:
            return
        
        # Only respond to threads started by or mentioning the bot
        # This prevents the bot from jumping into unrelated conversations
        try:
            bot_user_id = get_bot_user_id(client)
            # Fetch just the parent message
            result = client.conversations_replies(
                channel=channel,
                ts=thread_ts,
                limit=1,
                inclusive=True
            )
            parent = result.get("messages", [{}])[0]
            
            # Check if bot started the thread or was mentioned in the parent
            parent_by_bot = parent.get("user") == bot_user_id
            bot_mentioned = f"<@{bot_user_id}>" in parent.get("text", "")
            
            if not (parent_by_bot or bot_mentioned):
                return
        except Exception as e:
            logger.error(
                f"Error checking thread parent: {str(e)}",
                extra={'error': str(e)}
            )
            return  # Don't respond if we can't verify
        
        # Respond to thread replies in conversations the bot started or was mentioned in
        process_message(body, say, client)
        
    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"Error in handle_message: {error_msg}",
            extra={'error': error_msg}
        )


# Handle slash command (configurable via SLACK_SLASH_COMMAND env var)
@app.command(SLASH_COMMAND)
def handle_slash_command(ack, command, client, respond):
    """Handle slash command.
    
    Args:
        ack: Slack acknowledgment function
        command: Slash command payload
        client: Slack WebClient
        respond: Slack respond function
    """
    try:
        ack(":hourglass_flowing_sand: Checking with the Bedrock Quota Agent...")
        
        text = command.get("text", "").strip()
        channel = command.get("channel_id")
        user_id = command.get("user_id")
        
        if not text:
            respond(
                f"Usage: `{SLASH_COMMAND} <your question>`\n\n"
                "Examples:\n"
                f"• `{SLASH_COMMAND} check quotas in us-west-2`\n"
                f"• `{SLASH_COMMAND} metrics for claude sonnet 4.5`"
            )
            return
        
        # Trigger async processing - posts as a new message that can become a thread
        trigger_async_processing(text, channel, None, user_id, is_slash_command=True)
        
    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"Error in handle_slash_command: {error_msg}",
            extra={'error': error_msg}
        )
        sanitized_msg = sanitize_error_message(error_msg)
        respond(f"Sorry, I encountered an error: {sanitized_msg}")


# Lambda handler
handler = SlackRequestHandler(app=app)


def lambda_handler(event, context):
    """AWS Lambda entry point.
    
    Args:
        event: Lambda event payload
        context: Lambda context object
        
    Returns:
        Response dictionary with statusCode
    """
    import time
    start_time = time.time()
    request_id = context.aws_request_id if context else 'unknown'
    
    try:
        logger.info(
            "Received event",
            extra={'request_id': request_id}
        )
        
        # Check if this is an async processing request
        if event.get("async_process"):
            logger.info(
                "Processing async request",
                extra={'request_id': request_id}
            )
            process_async(event)
            
            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "Lambda execution completed",
                extra={'request_id': request_id, 'duration_ms': duration_ms}
            )
            return {"statusCode": 200}
        
        # Otherwise, handle as Slack event
        response = handler.handle(event, context)
        
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "Lambda execution completed",
            extra={
                'request_id': request_id,
                'duration_ms': duration_ms,
                'status_code': response.get('statusCode', 200)
            }
        )
        return response
        
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)
        logger.error(
            f"Unhandled error in lambda_handler: {error_msg}",
            extra={
                'error': error_msg,
                'request_id': request_id,
                'duration_ms': duration_ms
            }
        )
        # Return sanitized error message
        sanitized_msg = sanitize_error_message(error_msg)
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": "Internal server error",
                "message": sanitized_msg,
                "request_id": request_id
            })
        }
