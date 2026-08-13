"""Mock Slack client for integration testing."""
import logging
from typing import Optional, List, Dict
from core.slack_client import SlackClient

logger = logging.getLogger(__name__)


class MockSlackClient(SlackClient):
    """Mock Slack client that records API calls instead of making them."""
    
    def __init__(self, bot_user_id: str = "U_BOT_TEST"):
        """Initialize mock Slack client.
        
        Args:
            bot_user_id: Mock bot user ID for testing
        """
        self.bot_user_id = bot_user_id
        self.recorded_calls: List[Dict] = []
        self._message_counter = 0
    
    def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None
    ) -> str:
        """Record a message post (doesn't actually post to Slack).
        
        Args:
            channel: Slack channel ID
            text: Message text to post
            thread_ts: Optional thread timestamp for replies
            
        Returns:
            A mock timestamp
        """
        self._message_counter += 1
        mock_ts = f"1234567890.{self._message_counter:06d}"
        
        # Record the call
        call_record = {
            "method": "chat.postMessage",
            "channel": channel,
            "text": text,
            "thread_ts": thread_ts,
            "response_ts": mock_ts
        }
        self.recorded_calls.append(call_record)
        
        logger.info(
            f"[MOCK] Would post to Slack: channel={channel}, "
            f"thread_ts={thread_ts}, text_length={len(text)}"
        )
        
        return mock_ts
    
    def get_bot_user_id(self) -> str:
        """Get the mock bot's user ID.
        
        Returns:
            The mock bot's user ID
        """
        return self.bot_user_id
    
    def get_thread_parent(self, channel: str, thread_ts: str) -> dict:
        """Get a mock parent message.
        
        Args:
            channel: Slack channel ID
            thread_ts: Thread timestamp
            
        Returns:
            Mock parent message data
        """
        # Return a mock parent message
        mock_parent = {
            "user": self.bot_user_id,
            "text": f"Mock parent message for thread {thread_ts}",
            "ts": thread_ts
        }
        
        # Record the call
        call_record = {
            "method": "conversations.replies",
            "channel": channel,
            "ts": thread_ts,
            "response": mock_parent
        }
        self.recorded_calls.append(call_record)
        
        return mock_parent
    
    def get_recorded_calls(self) -> List[Dict]:
        """Get all recorded Slack API calls.
        
        Returns:
            List of recorded call dictionaries
        """
        return self.recorded_calls
    
    def clear_recorded_calls(self):
        """Clear all recorded calls."""
        self.recorded_calls = []
        self._message_counter = 0
