"""Property-based tests for API Gateway Stack parameter validation.

This module contains property-based tests that verify API Gateway Stack
parameter validation behavior across different configurations.
"""

from hypothesis import given, strategies as st, settings
from aws_cdk import App
from infra.stacks.slack_integration_stack import SlackIntegrationStack


# Strategy for generating valid AgentCore runtime ARNs
@st.composite
def agentcore_runtime_arn(draw):
    """Generate a valid AgentCore runtime ARN for testing.

    Returns a string representing a valid AWS ARN for an AgentCore runtime.
    Format: arn:aws:bedrock-agentcore:region:account:runtime/runtime-id
    """
    # Generate region (common AWS regions)
    region = draw(st.sampled_from([
        "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"
    ]))

    # Generate account ID (12 digits)
    account_id = draw(st.integers(min_value=100000000000, max_value=999999999999))

    # Generate runtime ID (alphanumeric string)
    runtime_id = draw(st.text(
        min_size=8,
        max_size=64,
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), blacklist_characters='-')
    ))

    return f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{runtime_id}"


@settings(
    deadline=None,  # CDK synthesis can be slow
    max_examples=10  # Test multiple combinations
)
@given(
    runtime_arn=agentcore_runtime_arn(),
)
def test_property_6_stack_default_secret_name(runtime_arn):
    """
    Property 6: Stack parameter validation - default secret name.

    For any SlackIntegrationStack initialization without an explicit
    slack_secret_name, the stack should use the default secret name
    pattern "bedrock-quota-agent/{environment}/slack-credentials".

    Feature: slack-secrets-manager-migration
    Property 6: Stack parameter validation
    Validates: Requirements 6.5
    """
    app = App()

    # Should not raise any exception; uses default secret name
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn=runtime_arn,
        # slack_secret_name intentionally omitted (uses default)
        dedup_table_name="test-dedup-table",
    )

    # Verify stack was created successfully
    assert stack is not None, "Stack should be created with default secret name"
    assert stack.slack_secret_name == "bedrock-quota-agent/dev/slack-credentials", (
        f"Default secret name should follow pattern, got: {stack.slack_secret_name}"
    )
    assert stack.slack_integration is not None, "Slack integration should be created"


@settings(
    deadline=None,  # CDK synthesis can be slow
    max_examples=5  # Test a few runtime ARNs
)
@given(
    runtime_arn=agentcore_runtime_arn()
)
def test_property_6_stack_parameter_validation_custom_secret_name(runtime_arn):
    """
    Property 6: Stack parameter validation - custom secret name.

    For any SlackIntegrationStack initialization with an explicit
    slack_secret_name, the stack should use that custom name instead
    of the default.

    Feature: slack-secrets-manager-migration
    Property 6: Stack parameter validation
    Validates: Requirements 6.5
    """
    app = App()

    custom_secret_name = "my-org/custom/slack-secret"

    # Should not raise any exception with custom secret name
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn=runtime_arn,
        slack_secret_name=custom_secret_name,
        dedup_table_name="test-dedup-table",
    )

    # Verify stack was created successfully with custom secret name
    assert stack is not None, "Stack should be created with custom secret name"
    assert stack.slack_secret_name == custom_secret_name, (
        f"Custom secret name should be stored, got: {stack.slack_secret_name}"
    )
    assert stack.slack_integration is not None, "Slack integration should be created"


@settings(
    deadline=None,  # CDK synthesis can be slow
    max_examples=5  # Test a few environment values
)
@given(
    environment=st.sampled_from(["dev", "staging", "prod"]),
    runtime_arn=agentcore_runtime_arn()
)
def test_property_6_stack_parameter_validation_environment_in_default_secret_name(environment, runtime_arn):
    """
    Property 6: Stack parameter validation - environment in default secret name.

    For any valid environment value, the default secret name should include
    the environment in its path, ensuring proper isolation.

    Feature: slack-secrets-manager-migration
    Property 6: Stack parameter validation
    Validates: Requirements 6.5, 7.1
    """
    app = App()

    stack = SlackIntegrationStack(
        app,
        f"TestStack-{environment}",
        environment=environment,
        runtime_arn=runtime_arn,
        # No slack_secret_name provided, uses default
        dedup_table_name="test-dedup-table",
    )

    # Verify default secret name includes environment
    expected_secret_name = f"bedrock-quota-agent/{environment}/slack-credentials"
    assert stack.slack_secret_name == expected_secret_name, (
        f"Default secret name should be '{expected_secret_name}', "
        f"got: '{stack.slack_secret_name}'"
    )
