"""AgentCore invocation client."""
import json
import logging
import boto3

logger = logging.getLogger(__name__)


class AgentCoreClient:
    """Client for invoking AgentCore runtime."""
    
    def __init__(self, runtime_arn: str, region: str):
        """Initialize AgentCore client.
        
        Args:
            runtime_arn: AgentCore runtime ARN
            region: AWS region
        """
        self.runtime_arn = runtime_arn
        self.region = region
        self.client = boto3.client('bedrock-agentcore', region_name=region)
    
    def invoke(
        self,
        prompt: str,
        session_id: str = None,
        actor_id: str = None
    ) -> str:
        """Invoke the AgentCore runtime with a prompt and optional session context.
        
        Args:
            prompt: The user's question or message
            session_id: Optional session identifier for conversation continuity
            actor_id: Optional actor identifier for per-user memory
            
        Returns:
            The agent's response text
            
        Raises:
            Exception: If invocation fails
        """
        # Build payload with Memory session context
        payload = {"prompt": prompt}
        if session_id:
            payload["session_id"] = session_id
        if actor_id:
            payload["actor_id"] = actor_id
        
        # Build invocation parameters
        invoke_params = {
            "agentRuntimeArn": self.runtime_arn,
            "payload": json.dumps(payload).encode()
        }
        
        # Log session context for debugging
        logger.info(
            "Invoking AgentCore",
            extra={
                'session_id': session_id,
                'actor_id': actor_id
            }
        )
        
        response = self.client.invoke_agent_runtime(**invoke_params)
        result = json.loads(response['response'].read().decode())
        return result.get("result", "No response from agent")
