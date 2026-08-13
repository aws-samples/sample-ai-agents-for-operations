"""Unit tests for memory resource construct."""

from aws_cdk import App, Stack
from aws_cdk.assertions import Template, Match
from infra.custom_constructs.application_stack.memory_resource import AgentMemoryResource


def test_memory_resource_description():
    """
    Verify that description contains "Bedrock Quota Agent".
    """
    # Create a test stack with the memory resource construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the memory resource construct
    AgentMemoryResource(
        stack,
        "TestMemoryResource",
        stack_name="test-stack"
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Verify that the custom resource has the correct description
    # Custom resources are of type AWS::CloudFormation::CustomResource
    template.has_resource_properties("AWS::CloudFormation::CustomResource", {
        "Description": Match.string_like_regexp(".*Bedrock Quota Agent.*")
    })
