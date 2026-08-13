"""Unit tests for SlackIntegrationStack integration validation."""

from aws_cdk import App
from infra.stacks.slack_integration_stack import SlackIntegrationStack


def test_stack_initializes_with_default_secret_name():
    """
    Test that stack initializes correctly without a custom slack_secret_name.

    When slack_secret_name is not provided, the stack should use the default
    pattern "bedrock-quota-agent/{environment}/slack-credentials" and
    initialize successfully.

    Validates: Requirements 6.5
    """
    app = App()

    # Should not raise any exception
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        # slack_secret_name intentionally omitted (uses default)
        dedup_table_name="test-dedup-table",
    )

    # Verify stack was created successfully
    assert stack is not None
    assert stack.slack_secret_name == "bedrock-quota-agent/dev/slack-credentials"
    assert stack.slack_integration is not None


def test_stack_initializes_with_custom_secret_name():
    """
    Test that stack initializes correctly with a custom slack_secret_name.

    When slack_secret_name is provided, the stack should use that custom name
    and initialize successfully.

    Validates: Requirements 6.5
    """
    app = App()

    custom_secret = "my-org/custom-slack-credentials"

    # Should not raise any exception
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        slack_secret_name=custom_secret,
        dedup_table_name="test-dedup-table",
    )

    # Verify stack was created successfully with custom secret name
    assert stack is not None
    assert stack.slack_secret_name == custom_secret
    assert stack.slack_integration is not None


def test_default_secret_name_varies_by_environment():
    """
    Test that the default secret name includes the environment.

    Different environments should produce different default secret names,
    ensuring proper isolation between environments.

    Validates: Requirements 7.1, 7.2
    """
    environments = ["dev", "staging", "prod"]

    for env in environments:
        app = App()
        stack = SlackIntegrationStack(
            app,
            f"TestStack-{env}",
            environment=env,
            runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
            dedup_table_name="test-dedup-table",
        )

        expected_secret_name = f"bedrock-quota-agent/{env}/slack-credentials"
        assert stack.slack_secret_name == expected_secret_name, (
            f"Expected secret name '{expected_secret_name}' for environment '{env}', "
            f"got '{stack.slack_secret_name}'"
        )
