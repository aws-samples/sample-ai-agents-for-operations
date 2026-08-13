"""Slack event processing logic."""
import logging

logger = logging.getLogger(__name__)


class SlackEventProcessor:
    """Process Slack events and extract relevant data."""
    
    def parse_event(self, body: dict) -> dict:
        """Parse Slack event and extract message data.
        
        Args:
            body: Slack event body
            
        Returns:
            Dictionary with extracted fields: text, channel, thread_ts, user_id
        """
        event_data = extract_message_data(body)
        # Clean bot mention from text
        event_data['text'] = clean_mention_from_text(event_data['text'])
        return event_data


def extract_message_data(body: dict) -> dict:
    """Extract relevant data from Slack message event.
    
    Args:
        body: Slack event body
        
    Returns:
        Dictionary with extracted fields: text, channel, thread_ts, user_id, channel_type
    """
    event = body.get("event", {})
    
    return {
        "text": event.get("text", ""),
        "channel": event.get("channel"),
        "thread_ts": event.get("thread_ts") or event.get("ts"),
        "ts": event.get("ts"),
        "user_id": event.get("user"),
        "channel_type": event.get("channel_type"),
        "bot_id": event.get("bot_id"),
        "subtype": event.get("subtype")
    }


def extract_slash_command_data(command: dict) -> dict:
    """Extract relevant data from Slack slash command.
    
    Args:
        command: Slash command payload
        
    Returns:
        Dictionary with extracted fields: text, channel, user_id
    """
    return {
        "text": command.get("text", "").strip(),
        "channel": command.get("channel_id"),
        "user_id": command.get("user_id")
    }


def clean_mention_from_text(text: str) -> str:
    """Remove bot mention from text if present.
    
    Args:
        text: Original message text
        
    Returns:
        Text with bot mention removed
    """
    if "<@" in text:
        return text.split(">", 1)[-1].strip()
    return text


def should_skip_message(event_data: dict) -> bool:
    """Determine if a message event should be skipped.
    
    Args:
        event_data: Extracted event data from extract_message_data
        
    Returns:
        True if message should be skipped, False otherwise
    """
    # Skip bot messages and message subtypes (edits, deletes, etc.)
    if event_data.get("bot_id") or event_data.get("subtype"):
        return True
    
    return False


def is_direct_message(event_data: dict) -> bool:
    """Check if message is a direct message.
    
    Args:
        event_data: Extracted event data from extract_message_data
        
    Returns:
        True if message is a DM, False otherwise
    """
    return event_data.get("channel_type") == "im"


def is_thread_reply(event_data: dict) -> bool:
    """Check if message is a thread reply (not a top-level message).
    
    Args:
        event_data: Extracted event data from extract_message_data
        
    Returns:
        True if message is a thread reply, False otherwise
    """
    thread_ts = event_data.get("thread_ts")
    ts = event_data.get("ts")
    
    # It's a thread reply if thread_ts exists and is different from ts
    return thread_ts and thread_ts != ts


def has_mention(text: str) -> bool:
    """Check if text contains any user mention.
    
    Args:
        text: Message text
        
    Returns:
        True if text contains a mention, False otherwise
    """
    return "<@" in text
