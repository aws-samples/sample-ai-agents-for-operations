"""Memory resource construct for Bedrock Quota Agent."""

import os
from pathlib import Path

from aws_cdk import CustomResource, Duration, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import custom_resources as cr
from constructs import Construct


class AgentMemoryResource(Construct):
    """Create AgentCore Memory resource for conversation context."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        stack_name: str,
        description: str = None,
    ) -> None:
        """
        Create AgentCore Memory resource.

        Args:
            scope: Parent construct
            construct_id: Unique identifier
            stack_name: Stack name for resource naming
            description: Optional description for the memory resource
        """
        super().__init__(scope, construct_id)

        # Use provided description or default
        memory_description = description or "Long-term memory for Bedrock Quota Agent"

        # Create custom resource provider
        provider = self._create_provider()

        # Create custom resource for AgentCore Memory
        self.memory = CustomResource(
            self,
            "Memory",
            service_token=provider.service_token,
            properties={
                "MemoryName": f"{stack_name}-memory",
                "Description": memory_description,
            },
        )

        # Expose memory attributes
        self.memory_id = self.memory.get_att_string("MemoryId")
        self.memory_arn = self.memory.get_att_string("MemoryArn")

    def _create_provider(self) -> cr.Provider:
        """Create custom resource provider for AgentCore Memory API calls."""
        # Create Lambda execution role
        lambda_role = iam.Role(
            self,
            "MemoryProviderRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # Add permissions for AgentCore Memory operations
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:CreateMemory",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:UpdateMemory",
                    "bedrock-agentcore:DeleteMemory",
                    "bedrock-agentcore:ListMemories",
                ],
                resources=["*"],
            )
        )

        # Get the path to the handler directory (same directory as this file)
        handler_dir = Path(__file__).parent

        # Create Lambda function for custom resource
        on_event_handler = lambda_.Function(
            self,
            "MemoryHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.on_event",
            code=lambda_.Code.from_asset(str(handler_dir)),
            timeout=Duration.minutes(5),
            role=lambda_role,
        )

        # Create custom resource provider
        provider = cr.Provider(
            self,
            "MemoryProvider",
            on_event_handler=on_event_handler,
        )

        return provider

