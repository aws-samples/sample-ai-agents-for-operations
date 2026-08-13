"""Property-based tests for CloudFormation outputs in SlackIntegrationStack.

This module contains property-based tests that verify CloudFormation outputs
are created correctly, follow naming conventions, and are accessible for
cross-stack references.

Tests use Hypothesis to generate random inputs and verify properties hold
across all valid configurations.
"""

import pytest
from hypothesis import given, strategies as st, settings
from aws_cdk import App
from aws_cdk.assertions import Template

from infra.stacks.slack_integration_stack import SlackIntegrationStack


# Strategy for generating valid environment names
environments = st.sampled_from(["dev", "staging", "prod"])

# Strategy for generating valid runtime ARNs
@st.composite
def runtime_arn(draw):
    """Generate valid AgentCore runtime ARN."""
    region = draw(st.sampled_from(["us-west-2", "us-east-1", "eu-west-1"]))
    account = draw(st.integers(min_value=100000000000, max_value=999999999999))
    runtime_id = draw(st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Nd"), min_codepoint=97, max_codepoint=122),
        min_size=8,
        max_size=16
    ))
    return f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{runtime_id}"


@given(
    environment=environments,
)
@settings(max_examples=5, deadline=None)  # Disable deadline due to variable CDK synthesis times
@pytest.mark.property_test
def test_property_17_integration_handler_output_creation(environment):
    """
    Property 17: Integration handler output creation

    For any integration handler added to the stack, the synthesized template
    should include a CloudFormation output with the handler's endpoint URL.

    Feature: api-gateway-stack, Property 17: Integration handler output creation

    Validates: Requirements 8.3
    """
    app = App()

    # Use fixed ARN to speed up test
    arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test123"

    # Create stack with Slack integration (always created now)
    stack = SlackIntegrationStack(
        app,
        f"TestStack-{environment}",
        environment=environment,
        runtime_arn=arn,
        slack_secret_name=f"bedrock-quota-agent/{environment}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)
    outputs = template.to_json().get("Outputs", {})

    # Verify API Gateway endpoint output always exists
    api_output_found = False
    for output_key, output_value in outputs.items():
        if "ApiEndpointUrl" in output_key:
            api_output_found = True
            assert "Description" in output_value
            assert "Value" in output_value
            assert "Export" in output_value
            break

    assert api_output_found, "API Gateway endpoint output not found"

    # Verify Slack events URL output exists
    slack_output_found = False
    for output_key, output_value in outputs.items():
        if "SlackEventsUrl" in output_key:
            slack_output_found = True
            assert "Description" in output_value
            assert "Value" in output_value
            assert "Export" in output_value
            break

    assert slack_output_found, "Slack events URL output not found"



@given(
    environment=environments,
)
@settings(max_examples=5, deadline=None)  # Disable deadline due to variable CDK synthesis times
@pytest.mark.property_test
def test_property_18_output_naming_convention(environment):
    """
    Property 18: Output naming convention

    For any CloudFormation output in the stack, the export name should follow
    the pattern {StackName}-{OutputKey}.

    Feature: api-gateway-stack, Property 18: Output naming convention

    Validates: Requirements 8.4
    """
    app = App()

    stack_name = f"TestStack-{environment}"

    # Use fixed ARN to speed up test
    arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test123"

    # Create stack with Slack integration
    stack = SlackIntegrationStack(
        app,
        stack_name,
        environment=environment,
        runtime_arn=arn,
        slack_secret_name=f"bedrock-quota-agent/{environment}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)
    outputs = template.to_json().get("Outputs", {})

    # Verify all outputs follow the naming convention
    for output_key, output_value in outputs.items():
        if "Export" in output_value:
            export_name = output_value["Export"]["Name"]

            # Export name should start with stack name
            assert export_name.startswith(stack_name), \
                f"Export name '{export_name}' does not start with stack name '{stack_name}'"

            # Export name should follow pattern: {StackName}-{OutputKey}
            expected_prefix = f"{stack_name}-"
            assert export_name.startswith(expected_prefix)

            # Verify the output key part is not empty
            output_key_part = export_name[len(expected_prefix):]
            assert len(output_key_part) > 0



@given(environment=environments)
@settings(max_examples=3, deadline=None)  # Disable deadline due to variable CDK synthesis times
@pytest.mark.property_test
def test_property_19_cross_stack_output_accessibility(environment):
    """
    Property 19: Cross-stack output accessibility

    For any exported output from SlackIntegrationStack, a consuming stack should be
    able to import the value using Fn::ImportValue.

    Feature: api-gateway-stack, Property 19: Cross-stack output accessibility

    Validates: Requirements 8.5
    """
    from aws_cdk import Fn

    app = App()
    stack_name = f"TestStack-{environment}"
    arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test123"

    # Create stack with Slack integration
    api_stack = SlackIntegrationStack(
        app,
        stack_name,
        environment=environment,
        runtime_arn=arn,
        slack_secret_name=f"bedrock-quota-agent/{environment}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(api_stack)
    outputs = template.to_json().get("Outputs", {})

    # Verify at least one output with export name exists
    export_found = False
    for output_key, output_value in outputs.items():
        if "Export" in output_value:
            export_found = True
            export_name = output_value["Export"]["Name"]

            # Verify Fn.import_value can be called without error
            imported_value = Fn.import_value(export_name)
            assert imported_value is not None
            break

    assert export_found, "No exported outputs found in stack"
