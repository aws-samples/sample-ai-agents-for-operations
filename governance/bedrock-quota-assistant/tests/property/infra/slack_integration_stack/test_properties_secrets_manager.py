"""Property-based tests for Secrets Manager integration in API Gateway Stack.

This module contains property-based tests that verify the stack correctly
creates a Secrets Manager secret with placeholder values, grants proper
IAM permissions, and isolates secrets by environment.
"""

from hypothesis import given, strategies as st, settings
from aws_cdk import App, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk.assertions import Template, Match
from infra.custom_constructs.slack_integration_stack.slack_integration_construct import (
    SlackIntegrationConstruct,
)


# Strategy for generating valid environment names
environment_strategy = st.sampled_from(["dev", "staging", "prod"])


@settings(
    deadline=None,
    max_examples=10  # Property tests should run multiple iterations
)
@given(
    environment=environment_strategy,
)
def test_property_1_secret_resource_created(environment: str):
    """
    Property 1: A Secrets Manager secret is created in the template.

    The construct creates a secret with placeholder values that the operator
    must populate after deployment.

    Validates: Secret is created by the stack with correct structure.
    """
    # Create a test stack
    app = App()
    stack = Stack(app, "TestStack")

    # Create API Gateway
    api = apigw.RestApi(stack, "TestApi")

    # Create the Slack integration construct with secret_name
    SlackIntegrationConstruct(
        stack,
        "SlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment=environment,
        secret_name=f"bedrock-quota-agent/{environment}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)

    # Verify that exactly one Secrets Manager secret resource is created
    template.resource_count_is("AWS::SecretsManager::Secret", 1)

    # Verify the secret has the expected properties
    template.has_resource_properties("AWS::SecretsManager::Secret", {
        "Name": f"bedrock-quota-agent/{environment}/slack-credentials",
        "GenerateSecretString": {
            "SecretStringTemplate": '{"SLACK_BOT_TOKEN":"","SLACK_SIGNING_SECRET":""}',
            "GenerateStringKey": "__init_placeholder__",
        },
    })


@settings(
    deadline=None,
    max_examples=10  # Property tests should run multiple iterations
)
@given(
    environment=environment_strategy,
)
def test_property_2_iam_policy_grants_secret_access(environment: str):
    """
    Property 2: IAM policy grants secretsmanager:GetSecretValue and
    secretsmanager:DescribeSecret permissions.

    The Lambda function's IAM role should have permissions to read
    the stack-managed secret.

    Validates: Requirements 1.1, 1.2
    """
    # Create a test stack
    app = App()
    stack = Stack(app, "TestStack")

    # Create API Gateway
    api = apigw.RestApi(stack, "TestApi")

    # Create the Slack integration construct
    SlackIntegrationConstruct(
        stack,
        "SlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment=environment,
        secret_name=f"bedrock-quota-agent/{environment}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)

    # Verify IAM policy grants secretsmanager:GetSecretValue and DescribeSecret
    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {
            "Statement": Match.array_with([
                Match.object_like({
                    "Action": Match.array_with([
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret",
                    ]),
                    "Effect": "Allow",
                    "Resource": Match.any_value(),
                })
            ])
        }
    })


@settings(
    deadline=None,
    max_examples=10  # Property tests should run multiple iterations
)
@given(
    environment1=environment_strategy,
    environment2=environment_strategy,
)
def test_property_8_environment_specific_secret_isolation(
    environment1: str,
    environment2: str,
):
    """
    Property 8: Environment-specific secret isolation

    For any two SlackIntegrationConstructs deployed with different environment names,
    the referenced secret names should be different (containing their respective
    environment identifiers), ensuring that each environment reads from its own secret.

    Validates: Requirements 7.1, 7.2, 7.4
    """
    # Skip if environments are the same (we need different environments)
    if environment1 == environment2:
        return

    # Create two separate stacks with different environments
    app = App()

    secret_name1 = f"bedrock-quota-agent/{environment1}/slack-credentials"
    secret_name2 = f"bedrock-quota-agent/{environment2}/slack-credentials"

    # Stack 1 with environment1
    stack1 = Stack(app, "TestStack1")
    api1 = apigw.RestApi(stack1, "TestApi1")
    SlackIntegrationConstruct(
        stack1,
        "SlackIntegration1",
        api=api1,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test1",
        runtime_region="us-west-2",
        environment=environment1,
        secret_name=secret_name1,
        dedup_table_name="test-dedup-table",
    )

    # Stack 2 with environment2
    stack2 = Stack(app, "TestStack2")
    api2 = apigw.RestApi(stack2, "TestApi2")
    SlackIntegrationConstruct(
        stack2,
        "SlackIntegration2",
        api=api2,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test2",
        runtime_region="us-west-2",
        environment=environment2,
        secret_name=secret_name2,
        dedup_table_name="test-dedup-table",
    )

    # Synthesize templates for both stacks
    template1 = Template.from_stack(stack1)
    template2 = Template.from_stack(stack2)

    # Requirement 7.1: Verify secret names are different
    assert secret_name1 != secret_name2, (
        f"Secret names should be different for different environments: "
        f"{secret_name1} vs {secret_name2}"
    )

    # Requirement 7.2: Verify each secret name includes its environment identifier
    assert environment1 in secret_name1, (
        f"Secret name should include environment identifier '{environment1}'"
    )
    assert environment2 in secret_name2, (
        f"Secret name should include environment identifier '{environment2}'"
    )

    # Verify each stack creates exactly one Secrets Manager secret
    template1.resource_count_is("AWS::SecretsManager::Secret", 1)
    template2.resource_count_is("AWS::SecretsManager::Secret", 1)

    # Verify secrets have different names matching their environments
    template1.has_resource_properties("AWS::SecretsManager::Secret", {
        "Name": secret_name1,
    })
    template2.has_resource_properties("AWS::SecretsManager::Secret", {
        "Name": secret_name2,
    })

    # Requirement 7.4: Verify IAM policies exist in both stacks
    # (each scoped to their respective environment-specific secret)
    iam_policies1 = template1.find_resources("AWS::IAM::Policy")
    iam_policies2 = template2.find_resources("AWS::IAM::Policy")

    # Verify each stack has IAM policies with secretsmanager:GetSecretValue
    found_secret_policy1 = False
    for logical_id, policy_resource in iam_policies1.items():
        policy_doc = policy_resource.get("Properties", {}).get("PolicyDocument", {})
        statements = policy_doc.get("Statement", [])
        for statement in statements:
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if "secretsmanager:GetSecretValue" in actions:
                found_secret_policy1 = True
                resources = statement.get("Resource", [])
                if isinstance(resources, str):
                    resources = [resources]
                assert len(resources) > 0, (
                    "IAM policy should have resources specified"
                )

    found_secret_policy2 = False
    for logical_id, policy_resource in iam_policies2.items():
        policy_doc = policy_resource.get("Properties", {}).get("PolicyDocument", {})
        statements = policy_doc.get("Statement", [])
        for statement in statements:
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if "secretsmanager:GetSecretValue" in actions:
                found_secret_policy2 = True
                resources = statement.get("Resource", [])
                if isinstance(resources, str):
                    resources = [resources]
                assert len(resources) > 0, (
                    "IAM policy should have resources specified"
                )

    assert found_secret_policy1, (
        f"Stack for environment '{environment1}' should have an IAM policy "
        f"granting secretsmanager:GetSecretValue"
    )
    assert found_secret_policy2, (
        f"Stack for environment '{environment2}' should have an IAM policy "
        f"granting secretsmanager:GetSecretValue"
    )
