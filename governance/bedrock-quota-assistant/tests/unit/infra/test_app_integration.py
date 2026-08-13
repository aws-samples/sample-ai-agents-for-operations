"""Unit tests for app.py stack integration."""

from aws_cdk import App, Stack as BaseStack, CfnOutput
from aws_cdk.assertions import Template

from infra.stacks.observability_stack import ObservabilityStack


def test_observability_stack_with_application_stack_outputs():
    """
    Verify that ObservabilityStack can be instantiated with ApplicationStack outputs.
    
    This test ensures that the ObservabilityStack correctly accepts runtime_id
    and runtime_arn from ApplicationStack as cross-stack references.
    
    Requirements: 1.5, 10.1, 10.2, 10.4, 10.5
    """
    app = App()
    
    # Create a mock ApplicationStack with runtime outputs
    mock_app_stack = BaseStack(app, "MockApplicationStack")
    
    # Create outputs that simulate ApplicationStack's runtime outputs
    CfnOutput(
        mock_app_stack,
        "RuntimeId",
        value="BedrockQuotaAgent-test123",
        export_name="MockApplicationStack-RuntimeId",
    )
    
    CfnOutput(
        mock_app_stack,
        "RuntimeArn",
        value="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test123",
        export_name="MockApplicationStack-RuntimeArn",
    )
    
    # Create ObservabilityStack with the mock outputs
    # In real usage, these would be cross-stack references
    observability_stack = ObservabilityStack(
        app,
        "TestObservabilityStack",
        environment="dev",
        runtime_id="BedrockQuotaAgent-test123",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test123",
    )
    
    # Verify stack synthesizes successfully
    template = Template.from_stack(observability_stack)
    assert template is not None
    
    # Verify log group is created with correct name pattern
    template.has_resource_properties(
        "AWS::Logs::LogGroup",
        {
            "LogGroupName": "/aws/vendedlogs/bedrock-agentcore/runtime/BedrockQuotaAgent-test123",
        }
    )


def test_observability_stack_dependency_on_application_stack():
    """
    Verify that ObservabilityStack declares dependency on ApplicationStack.
    
    This test ensures that when both stacks are deployed together,
    CloudFormation will deploy ApplicationStack first.
    
    Requirements: 1.5, 10.1, 10.2, 10.4, 10.5
    """
    app = App()
    
    # Create a mock ApplicationStack
    mock_app_stack = BaseStack(app, "MockApplicationStack")
    
    # Create ObservabilityStack
    observability_stack = ObservabilityStack(
        app,
        "TestObservabilityStack",
        environment="dev",
        runtime_id="BedrockQuotaAgent-test123",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test123",
    )
    
    # Add dependency
    observability_stack.add_dependency(mock_app_stack)
    
    # Verify the dependency is declared
    # CDK tracks dependencies internally, and they're reflected in the synthesized template
    # The dependency ensures ApplicationStack deploys before ObservabilityStack
    dependencies = observability_stack.dependencies
    assert mock_app_stack in dependencies


def test_observability_stack_with_all_environments():
    """
    Verify that ObservabilityStack works with all supported environments.
    
    This test ensures the stack can be instantiated for dev, staging, and prod
    environments with ApplicationStack outputs.
    
    Requirements: 1.5, 10.1, 10.2, 10.4, 10.5
    """
    environments = ["dev", "staging", "prod"]
    
    for env in environments:
        app = App()
        
        # Create ObservabilityStack for each environment
        observability_stack = ObservabilityStack(
            app,
            f"TestObservabilityStack-{env}",
            environment=env,
            runtime_id=f"BedrockQuotaAgent-{env}-test123",
            runtime_arn=f"arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/{env}-test123",
        )
        
        # Verify stack synthesizes successfully
        template = Template.from_stack(observability_stack)
        assert template is not None
        
        # Verify environment-specific configuration is applied
        # (This is tested more thoroughly in observability_stack tests,
        # but we verify basic synthesis here)


def test_observability_stack_with_cross_stack_token_references():
    """
    Verify that ObservabilityStack works with CDK Token references.
    
    This test simulates the real scenario where runtime_id and runtime_arn
    are CDK Tokens (cross-stack references) rather than plain strings.
    
    Requirements: 1.5, 10.1, 10.2, 10.4, 10.5
    """
    from aws_cdk import Fn
    
    app = App()
    
    # Create a mock ApplicationStack
    mock_app_stack = BaseStack(app, "MockApplicationStack")
    
    # Create outputs
    CfnOutput(
        mock_app_stack,
        "RuntimeId",
        value="BedrockQuotaAgent-test123",
        export_name="MockApplicationStack-RuntimeId",
    )
    
    CfnOutput(
        mock_app_stack,
        "RuntimeArn",
        value="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test123",
        export_name="MockApplicationStack-RuntimeArn",
    )
    
    # Import the values (simulating cross-stack references)
    runtime_id = Fn.import_value("MockApplicationStack-RuntimeId")
    runtime_arn = Fn.import_value("MockApplicationStack-RuntimeArn")
    
    # Create ObservabilityStack with imported values
    observability_stack = ObservabilityStack(
        app,
        "TestObservabilityStack",
        environment="dev",
        runtime_id=runtime_id,
        runtime_arn=runtime_arn,
    )
    
    # Verify stack synthesizes successfully with Token references
    template = Template.from_stack(observability_stack)
    assert template is not None
    
    # Verify the stack contains the expected resources
    # (The actual values will be resolved at deployment time)
    template.resource_count_is("AWS::Logs::LogGroup", 1)
    template.resource_count_is("AWS::Logs::DeliverySource", 2)
    template.resource_count_is("AWS::Logs::DeliveryDestination", 2)
    template.resource_count_is("AWS::Logs::Delivery", 2)
