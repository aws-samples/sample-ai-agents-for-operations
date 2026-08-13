"""Base construct for platform integration handlers.

This module provides the IntegrationHandlerConstruct base class for creating
platform-specific integration handlers that connect external platforms
(Slack, Teams, Discord, etc.) to the AgentCore runtime.

The base class handles common functionality like IAM permissions for AgentCore
invocation, while subclasses implement platform-specific logic.
"""
from aws_cdk import (
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_iam as iam,
)
from constructs import Construct


class IntegrationHandlerConstruct(Construct):
    """
    Base construct for platform-specific integration handlers.
    
    This abstract base class provides common functionality for integration handlers
    that connect external platforms (Slack, Teams, Discord, etc.) to the AgentCore runtime.
    
    The base class handles:
    - IAM permissions for invoking AgentCore runtime
    - Common configuration (runtime ARN, region, environment)
    - Extension points for platform-specific implementations
    
    Subclasses must implement:
    - _create_lambda_function(): Create the platform-specific Lambda function
    
    Subclasses can extend:
    - _create_iam_permissions(): Add platform-specific IAM permissions beyond AgentCore invocation
    """
    
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        api: apigw.RestApi,
        runtime_arn: str,
        runtime_region: str,
        environment: str,
        route_path: str,
        **kwargs
    ) -> None:
        """
        Initialize the integration handler construct.
        
        Args:
            scope: Parent construct
            construct_id: Unique identifier for this construct
            api: API Gateway REST API to add routes to
            runtime_arn: ARN of the AgentCore runtime to invoke
            runtime_region: AWS region of the AgentCore runtime
            environment: Deployment environment (dev, staging, prod)
            route_path: API Gateway route path (e.g., "/slack/events")
            **kwargs: Additional construct properties
        """
        super().__init__(scope, construct_id, **kwargs)
        
        self.api = api
        self.runtime_arn = runtime_arn
        self.runtime_region = runtime_region
        self.environment = environment
        self.route_path = route_path
        
        # To be set by subclasses when they create their Lambda function
        self.lambda_function = None
    
    def _grant_agentcore_permissions(self) -> None:
        """
        Grant permission to invoke AgentCore runtime and write CloudWatch Logs.
        
        This method adds the following permissions to the Lambda function's execution role:
        - bedrock-agentcore:InvokeAgentRuntime: Allows invoking the AgentCore runtime
        - logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents: CloudWatch Logs
        
        This should be called by subclasses after creating their Lambda function.
        """
        if self.lambda_function:
            # Grant AgentCore invocation permission
            # Need both base ARN and wildcard pattern to cover all endpoints
            self.lambda_function.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["bedrock-agentcore:InvokeAgentRuntime"],
                    resources=[
                        self.runtime_arn,
                        f"{self.runtime_arn}/*"
                    ],
                )
            )
            
            # Grant CloudWatch Logs permissions
            self.lambda_function.add_to_role_policy(
                iam.PolicyStatement(
                    actions=[
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    resources=[
                        f"arn:aws:logs:{self.runtime_region}:*:log-group:/aws/lambda/*"
                    ],
                )
            )
    
    def _create_lambda_function(self) -> lambda_.Function:
        """
        Create Lambda function (to be implemented by subclasses).
        
        Subclasses must implement this method to create their platform-specific
        Lambda function with appropriate handler code, environment variables,
        and configuration.
        
        Returns:
            The created Lambda function
            
        Raises:
            NotImplementedError: This method must be implemented by subclasses
        """
        raise NotImplementedError(
            "Subclasses must implement _create_lambda_function()"
        )
