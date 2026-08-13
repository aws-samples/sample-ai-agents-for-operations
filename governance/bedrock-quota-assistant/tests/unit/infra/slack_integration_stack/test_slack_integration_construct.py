"""Unit tests for SlackIntegrationConstruct."""

from aws_cdk import App, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk.assertions import Template, Match
from infra.custom_constructs.slack_integration_stack.slack_integration_construct import (
    SlackIntegrationConstruct,
)


def test_lambda_function_creation():
    """
    Verify that SlackIntegrationConstruct creates a Lambda function.
    """
    app = App()
    stack = Stack(app, "TestStack")

    # Create API Gateway
    api = apigw.RestApi(stack, "TestApi")

    # Create Slack integration
    SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="dev",
        secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify Lambda function is created
    template.resource_count_is("AWS::Lambda::Function", 1)


def test_lambda_environment_variables():
    """
    Verify that Lambda function has correct environment variables.
    """
    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="dev",
        secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify Lambda has SLACK_SECRET_ARN instead of credentials directly
    template.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {
            "Variables": {
                "SLACK_SECRET_ARN": Match.any_value(),
                "AGENTCORE_ARN": "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
                "AGENTCORE_REGION": "us-west-2",
                "ENVIRONMENT": "dev",
            }
        }
    })

    # Verify credentials are NOT in environment variables
    lambda_resources = template.find_resources("AWS::Lambda::Function")
    for resource_id, resource in lambda_resources.items():
        env_vars = resource.get("Properties", {}).get("Environment", {}).get("Variables", {})
        assert "SLACK_BOT_TOKEN" not in env_vars, "SLACK_BOT_TOKEN should not be in environment variables"
        assert "SLACK_SIGNING_SECRET" not in env_vars, "SLACK_SIGNING_SECRET should not be in environment variables"


def test_lambda_iam_permissions():
    """
    Verify that Lambda has correct IAM permissions.
    """
    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="dev",
        secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify IAM role has AgentCore invocation permission (Resource is now an array)
    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {
            "Statement": Match.array_with([
                Match.object_like({
                    "Action": "bedrock-agentcore:InvokeAgentRuntime",
                    "Effect": "Allow",
                    "Resource": Match.array_with([
                        Match.string_like_regexp(".*bedrock-agentcore.*runtime/test.*")
                    ]),
                })
            ])
        }
    })

    # Verify IAM role has Secrets Manager GetSecretValue permission
    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {
            "Statement": Match.array_with([
                Match.object_like({
                    "Action": Match.array_with([
                        "secretsmanager:GetSecretValue",
                    ]),
                    "Effect": "Allow",
                    "Resource": Match.any_value(),
                })
            ])
        }
    })

    # Verify IAM role has self-invocation permission
    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {
            "Statement": Match.array_with([
                Match.object_like({
                    "Action": "lambda:InvokeFunction",
                    "Effect": "Allow",
                })
            ])
        }
    })


def test_api_gateway_route_creation():
    """
    Verify that API Gateway route is created at /slack/events.
    """
    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="dev",
        secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify /slack resource is created
    template.has_resource_properties("AWS::ApiGateway::Resource", {
        "PathPart": "slack"
    })

    # Verify /events resource is created
    template.has_resource_properties("AWS::ApiGateway::Resource", {
        "PathPart": "events"
    })

    # Verify POST method is created
    template.has_resource_properties("AWS::ApiGateway::Method", {
        "HttpMethod": "POST"
    })


def test_lambda_layer_creation():
    """
    Verify that Lambda layer is created for Slack dependencies.
    """
    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="dev",
        secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify Lambda layer is created
    template.resource_count_is("AWS::Lambda::LayerVersion", 1)

    template.has_resource_properties("AWS::Lambda::LayerVersion", {
        "Description": "Slack Bolt SDK and Slack SDK dependencies",
        "CompatibleRuntimes": ["python3.11"]
    })


def test_secrets_manager_secret_created():
    """
    Verify that SlackIntegrationConstruct creates a Secrets Manager secret.
    """
    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="dev",
        secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # Verify exactly one secret is created
    template.resource_count_is("AWS::SecretsManager::Secret", 1)

    # Verify secret properties
    template.has_resource_properties("AWS::SecretsManager::Secret", {
        "Name": "bedrock-quota-agent/dev/slack-credentials",
        "GenerateSecretString": {
            "SecretStringTemplate": '{"SLACK_BOT_TOKEN":"","SLACK_SIGNING_SECRET":""}',
            "GenerateStringKey": "__init_placeholder__",
        },
    })


def test_secret_removal_policy_destroy_for_dev():
    """
    Verify dev environment uses DESTROY removal policy for secret.
    """
    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="dev",
        secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # DeletionPolicy: Delete corresponds to RemovalPolicy.DESTROY
    template.has_resource("AWS::SecretsManager::Secret", {
        "DeletionPolicy": "Delete",
    })


def test_secret_removal_policy_retain_for_prod():
    """
    Verify prod environment uses RETAIN removal policy for secret.
    """
    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="prod",
        secret_name="bedrock-quota-agent/prod/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)

    # DeletionPolicy: Retain corresponds to RemovalPolicy.RETAIN
    template.has_resource("AWS::SecretsManager::Secret", {
        "DeletionPolicy": "Retain",
    })


def test_events_url_attribute():
    """
    Verify that events_url attribute is accessible.
    """
    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    slack_integration = SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="dev",
        secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Verify events_url attribute exists
    assert hasattr(slack_integration, "events_url"), "SlackIntegrationConstruct should have 'events_url' attribute"
    assert slack_integration.events_url is not None, "events_url should not be None"
    assert "slack/events" in slack_integration.events_url, "events_url should contain 'slack/events'"


def test_cloudformation_template_synthesis():
    """
    Verify that SlackIntegrationConstruct synthesizes to valid CloudFormation template.
    """
    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    SlackIntegrationConstruct(
        stack,
        "TestSlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment="dev",
        secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Should not raise exception
    template = Template.from_stack(stack)

    # Verify expected resources are created
    template.resource_count_is("AWS::Lambda::Function", 1)
    template.resource_count_is("AWS::Lambda::LayerVersion", 1)
    # Note: API Gateway creates its own IAM role for CloudWatch logging,
    # so we expect 2 roles: one for Lambda, one for API Gateway
    template.resource_count_is("AWS::IAM::Role", 2)
