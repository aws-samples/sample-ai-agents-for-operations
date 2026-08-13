"""SSM parameters construct for Bedrock Quota Agent configuration."""

from typing import Dict
from aws_cdk import aws_ssm as ssm
from constructs import Construct


class AgentSsmParameters(Construct):
    """Create SSM parameters for agent configuration storage."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        parameters: Dict[str, str],
        parameter_namespace: str = "/bedrock-quota-agent/",
    ) -> None:
        """
        Create SSM parameters for the Bedrock Quota Agent.

        Args:
            scope: Parent construct
            construct_id: Unique identifier
            parameters: Dictionary mapping parameter names to their values
                       Example: {"memory-id": "mem-123", "region": "us-east-1"}
            parameter_namespace: SSM parameter namespace prefix
        """
        super().__init__(scope, construct_id)

        # Ensure namespace ends with /
        namespace = parameter_namespace.rstrip("/") + "/"

        # Store created parameters for reference
        self.parameters: Dict[str, ssm.StringParameter] = {}

        # Create SSM parameters from the provided map
        for param_name, param_value in parameters.items():
            # Generate a unique construct ID from the parameter name
            # Use the original parameter name to preserve case sensitivity
            # Replace special characters with underscores for valid construct IDs
            construct_id_suffix = param_name.replace("-", "_").replace(".", "_") + "Parameter"
            
            # Create the parameter
            parameter = ssm.StringParameter(
                self,
                construct_id_suffix,
                parameter_name=f"{namespace}{param_name}",
                string_value=param_value,
                description=f"{param_name.replace('-', ' ').title()} for Bedrock Quota Agent",
            )
            
            # Store reference using the parameter name as key
            self.parameters[param_name] = parameter
