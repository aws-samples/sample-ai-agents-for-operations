"""AgentCore runtime construct for Bedrock Quota Agent."""

from aws_cdk import CustomResource, Duration
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import custom_resources as cr
from constructs import Construct


class AgentCoreRuntime(Construct):
    """Deploy agent to AgentCore runtime."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        image_uri: str,
        role_arn: str,
        memory_id: str,
    ) -> None:
        """
        Create AgentCore runtime resource.

        Args:
            scope: Parent construct
            construct_id: Unique identifier
            image_uri: Docker image URI from ECR
            role_arn: IAM role ARN for agent permissions
            memory_id: Memory resource ID for conversation context
        """
        super().__init__(scope, construct_id)

        # Create custom resource provider
        provider = self._create_provider(role_arn)

        # Create custom resource for AgentCore runtime
        self.runtime = CustomResource(
            self,
            "Runtime",
            service_token=provider.service_token,
            properties={
                "ImageUri": image_uri,
                "RoleArn": role_arn,
                "MemoryId": memory_id,
                "Port": 8080,
                "HealthCheck": {
                    "Path": "/ping",
                    "IntervalSeconds": 30,
                    "TimeoutSeconds": 5,
                },
            },
        )

        # Expose runtime ARN
        self.runtime_arn = self.runtime.get_att_string("RuntimeArn")

    def _create_provider(self, agent_role_arn: str) -> cr.Provider:
        """Create custom resource provider for AgentCore runtime API calls."""
        # Create Lambda execution role
        lambda_role = iam.Role(
            self,
            "RuntimeProviderRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # Add permissions for AgentCore runtime operations
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:CreateAgentRuntime",
                    "bedrock-agentcore:CreateAgentRuntimeEndpoint",
                    "bedrock-agentcore:CreateWorkloadIdentity",
                    "bedrock-agentcore:GetAgentRuntime",
                    "bedrock-agentcore:UpdateAgentRuntime",
                    "bedrock-agentcore:DeleteAgentRuntime",
                    "bedrock-agentcore:DeleteAgentRuntimeEndpoint",
                    "bedrock-agentcore:DeleteWorkloadIdentity",
                    "bedrock-agentcore:ListAgentRuntimes",
                ],
                resources=["*"],
            )
        )
        
        # Add permission to pass the agent role to AgentCore
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[agent_role_arn],
            )
        )

        # Allow creating the service-linked role for AgentCore
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:CreateServiceLinkedRole"],
                resources=["arn:aws:iam::*:role/aws-service-role/*"],
            )
        )

        # Create Lambda function for custom resource
        on_event_handler = lambda_.Function(
            self,
            "RuntimeHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.on_event",
            code=lambda_.Code.from_inline(self._get_handler_code()),
            timeout=Duration.minutes(5),
            role=lambda_role,
        )

        # Create custom resource provider
        provider = cr.Provider(
            self,
            "RuntimeProvider",
            on_event_handler=on_event_handler,
        )

        return provider

    def _get_handler_code(self) -> str:
        """Get Lambda handler code for custom resource."""
        return """
import json
import boto3
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock = boto3.client('bedrock-agentcore-control')

def on_event(event, context):
    logger.info(f"Received event: {json.dumps(event)}")
    
    request_type = event['RequestType']
    props = event.get('ResourceProperties', {})
    
    try:
        if request_type == 'Create':
            return on_create(props, context)
        elif request_type == 'Update':
            return on_update(event['PhysicalResourceId'], props)
        elif request_type == 'Delete':
            return on_delete(event['PhysicalResourceId'])
        else:
            raise Exception(f"Unknown request type: {request_type}")
    except Exception as e:
        logger.error(f"Error in {request_type} operation: {str(e)}")
        # For DELETE operations, return success to avoid blocking stack deletion
        if request_type == 'Delete':
            logger.warning(f"DELETE failed but returning success to unblock CloudFormation")
            return {
                'PhysicalResourceId': event.get('PhysicalResourceId', 'unknown')
            }
        # For CREATE/UPDATE, re-raise to signal failure
        raise

def on_create(props, context):
    logger.info("Creating AgentCore runtime resource")
    
    image_uri = props.get('ImageUri')
    role_arn = props.get('RoleArn')
    memory_id = props.get('MemoryId')
    
    # Generate a unique runtime name
    stack_name = context.function_name.split('-')[0]
    runtime_name = f"{stack_name}Runtime"
    
    try:
        # Create runtime using AgentCore Control API
        response = bedrock.create_agent_runtime(
            agentRuntimeName=runtime_name,
            agentRuntimeArtifact={
                'containerConfiguration': {
                    'containerUri': image_uri
                }
            },
            roleArn=role_arn,
            networkConfiguration={
                'networkMode': 'PUBLIC'
            },
            description=f"Bedrock Quota Agent runtime for {stack_name}"
        )
        
        runtime_arn = response['agentRuntimeArn']
        runtime_id = response['agentRuntimeId']
        
        logger.info(f"Created runtime: {runtime_arn}")
        
        return {
            'PhysicalResourceId': runtime_id,
            'Data': {
                'RuntimeArn': runtime_arn,
                'RuntimeId': runtime_id
            }
        }
    except Exception as e:
        logger.error(f"Failed to create runtime: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Image URI: {image_uri}")
        logger.error(f"Role ARN: {role_arn}")
        raise

def on_update(physical_id, props):
    logger.info(f"Updating AgentCore runtime resource: {physical_id}")
    
    image_uri = props.get('ImageUri')
    role_arn = props.get('RoleArn')
    
    try:
        # Update runtime configuration
        response = bedrock.update_agent_runtime(
            agentRuntimeId=physical_id,
            agentRuntimeArtifact={
                'containerConfiguration': {
                    'containerUri': image_uri
                }
            },
            roleArn=role_arn,
            networkConfiguration={
                'networkMode': 'PUBLIC'
            }
        )
        
        runtime_arn = response.get('agentRuntimeArn')
        
        logger.info(f"Updated runtime: {runtime_arn}")
        
        return {
            'PhysicalResourceId': physical_id,
            'Data': {
                'RuntimeArn': runtime_arn,
                'RuntimeId': physical_id
            }
        }
    except Exception as e:
        logger.error(f"Failed to update runtime: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Runtime ID: {physical_id}")
        raise

def on_delete(physical_id):
    logger.info(f"Deleting AgentCore runtime resource: {physical_id}")
    
    try:
        # Delete runtime
        bedrock.delete_agent_runtime(agentRuntimeId=physical_id)
        
        logger.info(f"Deleted runtime: {physical_id}")
        
        return {
            'PhysicalResourceId': physical_id
        }
    except Exception as e:
        # Check if it's a ResourceNotFoundException
        if 'ResourceNotFoundException' in str(e):
            logger.info(f"Runtime {physical_id} not found, assuming already deleted")
            return {
                'PhysicalResourceId': physical_id
            }
        # Log error but return success to avoid blocking CloudFormation
        logger.error(f"Failed to delete runtime: {str(e)}")
        logger.warning(f"Returning success despite error to unblock CloudFormation")
        return {
            'PhysicalResourceId': physical_id
        }
"""
