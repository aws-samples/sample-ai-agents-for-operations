"""Abstract interface for Slack operations."""
from abc import ABC, abstractmethod
from typing import Optional


class SlackClient(ABC):
    """Abstract base class for Slack client implementations."""
    
    @abstractmethod
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
        pass
    
    @abstractmethod
    def get_bot_user_id(self) -> str:
        """Get the bot's user ID.
        
        Returns:
            The bot's user ID
            
        Raises:
            Exception: If auth test fails
        """
        pass
    
    @abstractmethod
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
        pass
