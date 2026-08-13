"""Production Slack client using real Slack SDK."""
import logging
from typing import Optional
from slack_sdk import WebClient
from core.slack_client import SlackClient
from core.markdown_to_slack import markdown_to_slack

logger = logging.getLogger(__name__)


class RealSlackClient(SlackClient):
    """Production Slack client that makes real API calls."""
    
    def __init__(self, token: str):
        """Initialize real Slack client.
        
        Args:
            token: Slack bot token
        """
        self.client = WebClient(token=token)
        self._bot_user_id = None
    
    def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None
    ) -> str:
        """Post a message to Slack.
        
        Args:
            channel: Slack channel ID
            text: Message text to post
            thread_ts: Optional thread timestamp for replies
            
        Returns:
            The timestamp of the posted message
            
        Raises:
            Exception: If posting fails
        """
        result_ts = None
        
        # Convert markdown to Slack mrkdwn
        slack_text = markdown_to_slack(text)
        
        # Handle long messages by chunking
        if len(slack_text) > 3000:
            chunks = [slack_text[i:i+3000] for i in range(0, len(slack_text), 3000)]
            for chunk in chunks:
                response = self.client.chat_postMessage(
                    channel=channel,
                    text=chunk,
                    thread_ts=thread_ts,
                    mrkdwn=True
                )
                if result_ts is None:
                    result_ts = response.get("ts")
        else:
            response = self.client.chat_postMessage(
                channel=channel,
                text=slack_text,
                thread_ts=thread_ts,
                mrkdwn=True
            )
            result_ts = response.get("ts")
        
        return result_ts
    
    def get_bot_user_id(self) -> str:
        """Get the bot's user ID (cached).
        
        Returns:
            The bot's user ID
            
        Raises:
            Exception: If auth test fails
        """
        if self._bot_user_id is None:
            self._bot_user_id = self.client.auth_test()["user_id"]
        return self._bot_user_id
    
    def get_thread_parent(self, channel: str, thread_ts: str) -> dict:
        """Get the parent message of a thread.
        
        Args:
            channel: Slack channel ID
            thread_ts: Thread timestamp
            
        Returns:
            Dictionary with parent message data (user, text, etc.)
            
        Raises:
            Exception: If fetching fails
        """
        result = self.client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            limit=1,
            inclusive=True
        )
        messages = result.get("messages", [])
        if not messages:
            raise Exception(f"No parent message found for thread {thread_ts}")
        
        return messages[0]
