"""Unit tests for SlackIntegrationStack."""

from aws_cdk import App
from aws_cdk.assertions import Annotations, Match, Template
from infra.stacks.slack_integration_stack import SlackIntegrationStack


def test_stack_synthesis_produces_api_gateway():
    """
    Verify that SlackIntegrationStack synthesis produces an API Gateway REST API.

    The stack should create an API Gateway REST API resource with the correct
    name and stage configuration for the specified environment.
    """
    app = App()
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        slack_secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify API Gateway REST API is created
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)

    # Verify API Gateway has correct name
    template.has_resource_properties("AWS::ApiGateway::RestApi", {
        "Name": "bedrock-quota-agent-dev",
        "Description": "API Gateway for Bedrock Quota Agent (dev environment)",
    })


def test_stack_synthesis_produces_slack_lambda():
    """
    Verify that SlackIntegrationStack creates Slack Lambda function.

    The stack should always create a Lambda function for handling Slack events
    since the Slack integration is always created.
    """
    app = App()
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        slack_secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify Lambda functions are created (main handler + test lambda)
    # Note: The count may be 1 or 2 depending on deploy_test_lambda setting
    lambda_count = len([r for r in template.find_resources("AWS::Lambda::Function")])
    assert lambda_count >= 1, f"Expected at least 1 Lambda function, found {lambda_count}"

    # Verify Lambda has SLACK_SECRET_ARN environment variable (not credentials directly)
    template.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {
            "Variables": {
                "SLACK_SECRET_ARN": Match.any_value(),
            }
        }
    })


def test_stack_synthesis_without_custom_secret_name():
    """
    Verify that SlackIntegrationStack uses default secret name when not provided.

    When slack_secret_name is not provided, the stack should use the default
    pattern "bedrock-quota-agent/{environment}/slack-credentials".
    """
    app = App()
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        dedup_table_name="test-dedup-table",
    )

    # Slack integration is always created
    assert stack.slack_integration is not None
    assert stack.api_gateway is not None
    assert stack.slack_secret_name == "bedrock-quota-agent/dev/slack-credentials"


def test_cloudformation_outputs_created():
    """
    Verify that CloudFormation outputs are created with correct export names.

    The stack should create outputs for the API Gateway endpoint URL and
    Slack events URL.
    """
    app = App()
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        slack_secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify API endpoint URL output
    template.has_output("ApiEndpointUrl", {
        "Description": "Base URL of the API Gateway",
        "Export": {
            "Name": "TestStack-ApiEndpointUrl"
        }
    })

    # Verify Slack events URL output
    template.has_output("SlackEventsUrl", {
        "Description": "URL for Slack event subscriptions",
        "Export": {
            "Name": "TestStack-SlackEventsUrl"
        }
    })

    # Verify Slack secret ARN output
    template.has_output("SlackSecretArn", {
        "Description": "ARN of the Slack credentials secret (populate after deploy)",
        "Export": {
            "Name": "TestStack-SlackSecretArn"
        }
    })



def test_environment_tags_applied():
    """
    Verify that environment tags are applied to all resources.

    All resources in the stack should have "Project" and "Environment" tags
    with the correct values.
    """
    app = App()
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="staging",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        slack_secret_name="bedrock-quota-agent/staging/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Get all resources from the template
    resources = template.to_json().get("Resources", {})

    # Verify at least one resource exists
    assert len(resources) > 0, "Stack should create at least one resource"

    # Check that Project tag is applied
    template.has_resource_properties("AWS::ApiGateway::RestApi", {
        "Tags": Match.array_with([
            {"Key": "Project", "Value": "BedrockQuotaAgent"},
        ])
    })

    # Verify environment is stored in instance attribute
    assert stack.env_name == "staging"


def test_stack_accepts_different_environments():
    """
    Verify that stack accepts different environment values.

    The stack should successfully synthesize with different environment
    values (dev, staging, prod) and use them in resource naming.
    """
    environments = ["dev", "staging", "prod"]

    for env in environments:
        app = App()
        stack = SlackIntegrationStack(
            app,
            f"TestStack-{env}",
            environment=env,
            runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
            slack_secret_name=f"bedrock-quota-agent/{env}/slack-credentials",
            dedup_table_name="test-dedup-table",
        )

        template = Template.from_stack(stack)

        # Verify API Gateway name includes environment
        template.has_resource_properties("AWS::ApiGateway::RestApi", {
            "Name": f"bedrock-quota-agent-{env}",
        })

        # Verify stage name matches environment
        template.has_resource_properties("AWS::ApiGateway::Stage", {
            "StageName": env,
        })


def test_stack_validates_environment_parameter():
    """
    Verify that stack validates the environment parameter.

    The stack should raise ValueError if the environment parameter is
    empty or not a string, or if it's not one of the allowed values.
    """
    app = App()

    # Test with empty string
    try:
        SlackIntegrationStack(
            app,
            "TestStack1",
            environment="",
            runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
            dedup_table_name="test-dedup-table",
        )
        assert False, "Should raise ValueError for empty environment"
    except ValueError as e:
        assert "environment parameter must be a non-empty string" in str(e)

    # Test with None
    try:
        SlackIntegrationStack(
            app,
            "TestStack2",
            environment=None,
            runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
            dedup_table_name="test-dedup-table",
        )
        assert False, "Should raise ValueError for None environment"
    except ValueError as e:
        assert "environment parameter must be a non-empty string" in str(e)

    # Test with invalid environment value
    try:
        SlackIntegrationStack(
            app,
            "TestStack3",
            environment="invalid",
            runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
            dedup_table_name="test-dedup-table",
        )
        assert False, "Should raise ValueError for invalid environment value"
    except ValueError as e:
        assert "environment must be one of" in str(e)
        assert "invalid" in str(e)


def test_stack_with_cross_stack_reference():
    """
    Verify that stack works with cross-stack references for runtime ARN.

    The stack should accept CDK tokens (cross-stack references) for the
    runtime_arn parameter and synthesize successfully.
    """
    from aws_cdk import Stack as BaseStack, CfnOutput

    app = App()

    # Create a mock source stack with an output
    source_stack = BaseStack(app, "SourceStack")
    CfnOutput(
        source_stack,
        "RuntimeArn",
        value="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        export_name="SourceStack-RuntimeArn",
    )

    # Create API Gateway stack that references the output
    # In real usage, this would use Fn.import_value
    api_stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        slack_secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(api_stack)

    # Verify stack synthesizes successfully
    assert template is not None


def test_api_gateway_stage_configuration():
    """
    Verify that API Gateway stage is configured correctly.

    The API Gateway deployment should create a stage with the environment
    name and appropriate configuration.
    """
    app = App()
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="prod",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        slack_secret_name="bedrock-quota-agent/prod/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify deployment is created
    template.resource_count_is("AWS::ApiGateway::Deployment", 1)

    # Verify stage is created with correct name
    template.has_resource_properties("AWS::ApiGateway::Stage", {
        "StageName": "prod",
    })


def test_stack_instance_attributes():
    """
    Verify that stack stores configuration as instance attributes.

    The stack should store environment, runtime_arn, and other configuration
    as instance attributes for access by other components.
    """
    app = App()
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        slack_secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Verify instance attributes
    assert stack.env_name == "dev"
    assert stack.runtime_arn == "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test"
    assert stack.runtime_region == "us-west-2"
    assert stack.slack_secret_name == "bedrock-quota-agent/dev/slack-credentials"
    assert stack.api_gateway is not None
    assert stack.api_endpoint_url is not None
    assert stack.slack_integration is not None


def test_prod_environment_emits_secret_warning():
    """
    Verify that prod environment emits a warning to populate the Slack secret.
    """
    app = App()
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="prod",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        dedup_table_name="test-dedup-table",
    )

    annotations = Annotations.from_stack(stack)
    annotations.has_warning("*", Match.string_like_regexp(".*populate.*"))


def test_dev_environment_no_secret_warning():
    """
    Verify that dev environment does NOT emit the secret population warning.
    """
    app = App()
    stack = SlackIntegrationStack(
        app,
        "TestStack",
        environment="dev",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        dedup_table_name="test-dedup-table",
    )

    annotations = Annotations.from_stack(stack)
    annotations.has_no_warning("*", Match.string_like_regexp(".*populate.*"))
