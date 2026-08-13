"""Session context management for AgentCore Memory."""
import logging

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages session context for AgentCore Memory integration."""
    
    def sanitize_session_id(self, thread_ts: str) -> str:
        """Sanitize thread timestamp for use as session ID.
        
        AgentCore Memory doesn't support dots in session IDs.
        Slack thread_ts format is "1739367890.123456" - replace dot with dash.
        
        Args:
            thread_ts: Slack thread timestamp
            
        Returns:
            Sanitized session ID
        """
        if not thread_ts:
            return None
        return thread_ts.replace(".", "-")
    
    def build_session_context(
        self,
        thread_ts: str = None,
        user_id: str = None,
        is_slash_command: bool = False
    ) -> dict:
        """Build session context for AgentCore invocation.
        
        Session management works at two levels:
        1. runtimeSessionId - AgentCore Runtime session (ephemeral compute context)
        2. session_id/actor_id in payload - AgentCore Memory (persistent conversation history)
        
        For slash commands, session_id is None initially and gets set from the posted
        message's timestamp to enable thread-based follow-ups.
        
        Args:
            thread_ts: Slack thread timestamp
            user_id: Slack user ID
            is_slash_command: Whether this is from a slash command
            
        Returns:
            Dictionary with session_id and actor_id (None values omitted)
        """
        context = {}
        
        # For slash commands, session_id will be set later from the posted message
        if not is_slash_command and thread_ts:
            context["session_id"] = self.sanitize_session_id(thread_ts)
        
        # Slack user = actor (enables per-user long-term memory)
        if user_id:
            context["actor_id"] = user_id
        
        return context


# Standalone function for backward compatibility with test_handler.py
def sanitize_session_id(thread_ts: str) -> str:
    """Sanitize thread timestamp for use as session ID.
    
    AgentCore Memory doesn't support dots in session IDs.
    Slack thread_ts format is "1739367890.123456" - replace dot with dash.
    
    Args:
        thread_ts: Slack thread timestamp
        
    Returns:
        Sanitized session ID
    """
    if not thread_ts:
        return None
    return thread_ts.replace(".", "-")
