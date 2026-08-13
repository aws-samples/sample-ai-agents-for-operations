"""Unit tests for AgentCore runtime construct."""

from aws_cdk import App, Stack
from aws_cdk.assertions import Template
from infra.custom_constructs.application_stack.agentcore_runtime import AgentCoreRuntime


def test_runtime_port_configuration():
    """
    Verify that runtime exposes port 8080.
    """
    # Create a test stack with the runtime construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the runtime construct
    AgentCoreRuntime(
        stack,
        "TestRuntime",
        image_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/bedrock-quota-agent:latest",
        role_arn="arn:aws:iam::123456789012:role/test-role",
        memory_id="test-memory-id"
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Verify that the custom resource has Port set to 8080
    # Custom resources are of type AWS::CloudFormation::CustomResource
    template.has_resource_properties("AWS::CloudFormation::CustomResource", {
        "Port": 8080
    })


def test_health_check_configuration():
    """
    Verify that health check uses /ping endpoint.
    """
    # Create a test stack with the runtime construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the runtime construct
    AgentCoreRuntime(
        stack,
        "TestRuntime",
        image_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/bedrock-quota-agent:latest",
        role_arn="arn:aws:iam::123456789012:role/test-role",
        memory_id="test-memory-id"
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Verify that the custom resource has HealthCheck configured with /ping endpoint
    template.has_resource_properties("AWS::CloudFormation::CustomResource", {
        "HealthCheck": {
            "Path": "/ping",
            "IntervalSeconds": 30,
            "TimeoutSeconds": 5
        }
    })
