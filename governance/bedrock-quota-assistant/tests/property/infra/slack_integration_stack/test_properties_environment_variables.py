"""Property-based tests for Lambda environment variables.

This module contains property-based tests that verify Lambda environment
variables exclude credentials and use Secrets Manager ARN instead.
"""

from hypothesis import given, strategies as st, settings
from aws_cdk import App, Stack
from aws_cdk.assertions import Template
from aws_cdk import aws_apigateway as apigw


@settings(
    deadline=None,
    max_examples=2      # Reduced for expensive CDK synthesis
)
@given(
    runtime_arn=st.text(min_size=10, max_size=100).map(
        lambda s: f"arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/{s.replace(':', '-')}"
    ),
    runtime_region=st.sampled_from(["us-west-2", "us-east-1", "eu-west-1"]),
    environment=st.sampled_from(["dev", "staging", "prod"]),
)
def test_property_4_environment_variables_exclude_credentials(
    runtime_arn: str,
    runtime_region: str,
    environment: str,
):
    """
    Property 4: Environment variables exclude credentials

    For any Lambda function created by SlackIntegrationConstruct, the environment
    variables should contain "SLACK_SECRET_ARN" but should not contain
    "SLACK_BOT_TOKEN" or "SLACK_SIGNING_SECRET".

    Validates: Requirements 4.1, 4.2, 4.3
    """
    from infra.custom_constructs.slack_integration_stack.slack_integration_construct import SlackIntegrationConstruct

    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(
        stack,
        "TestApi",
        rest_api_name="test-api",
    )

    secret_name = f"bedrock-quota-agent/{environment}/slack-credentials"

    SlackIntegrationConstruct(
        stack,
        "SlackIntegration",
        api=api,
        runtime_arn=runtime_arn,
        runtime_region=runtime_region,
        environment=environment,
        secret_name=secret_name,
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)
    template_dict = template.to_json()

    lambda_functions = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::Lambda::Function"
    }

    assert len(lambda_functions) > 0, "No Lambda function found in template"

    for lambda_logical_id, lambda_resource in lambda_functions.items():
        env_vars = lambda_resource.get("Properties", {}).get("Environment", {}).get("Variables", {})

        assert "SLACK_SECRET_ARN" in env_vars, (
            f"Lambda function {lambda_logical_id} is missing SLACK_SECRET_ARN environment variable. "
            f"Found environment variables: {list(env_vars.keys())}"
        )

        assert "SLACK_BOT_TOKEN" not in env_vars, (
            f"Lambda function {lambda_logical_id} should not have SLACK_BOT_TOKEN in environment variables. "
            f"Credentials should be retrieved from Secrets Manager. "
            f"Found environment variables: {list(env_vars.keys())}"
        )

        assert "SLACK_SIGNING_SECRET" not in env_vars, (
            f"Lambda function {lambda_logical_id} should not have SLACK_SIGNING_SECRET in environment variables. "
            f"Credentials should be retrieved from Secrets Manager. "
            f"Found environment variables: {list(env_vars.keys())}"
        )

        # The value should reference the secret ARN (either a Fn::Join construct or a string)
        secret_arn_value = env_vars["SLACK_SECRET_ARN"]
        assert secret_arn_value, "SLACK_SECRET_ARN should not be empty"



@settings(
    deadline=None,
    max_examples=2      # Reduced for expensive CDK synthesis
)
@given(
    runtime_arn=st.text(min_size=10, max_size=100).map(
        lambda s: f"arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/{s.replace(':', '-')}"
    ),
    runtime_region=st.sampled_from(["us-west-2", "us-east-1", "eu-west-1"]),
    environment=st.sampled_from(["dev", "staging", "prod"]),
)
def test_property_5_test_lambda_uses_same_credential_pattern(
    runtime_arn: str,
    runtime_region: str,
    environment: str,
):
    """
    Property 5: Test Lambda uses same credential pattern

    For any SlackIntegrationConstruct with deploy_test_lambda=True, both the main
    Lambda and test Lambda should have identical environment variable patterns for
    credential retrieval.

    Validates: Requirements 4.4
    """
    from infra.custom_constructs.slack_integration_stack.slack_integration_construct import SlackIntegrationConstruct

    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(
        stack,
        "TestApi",
        rest_api_name="test-api",
    )

    secret_name = f"bedrock-quota-agent/{environment}/slack-credentials"

    SlackIntegrationConstruct(
        stack,
        "SlackIntegration",
        api=api,
        runtime_arn=runtime_arn,
        runtime_region=runtime_region,
        environment=environment,
        secret_name=secret_name,
        deploy_test_lambda=True,
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)
    template_dict = template.to_json()

    lambda_functions = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::Lambda::Function"
    }

    assert len(lambda_functions) >= 2, (
        f"Expected at least 2 Lambda functions (main and test), "
        f"but found {len(lambda_functions)}"
    )

    lambda_env_vars = {}
    for lambda_logical_id, lambda_resource in lambda_functions.items():
        env_vars = lambda_resource.get("Properties", {}).get("Environment", {}).get("Variables", {})
        lambda_env_vars[lambda_logical_id] = env_vars

    env_var_keys_list = [set(env_vars.keys()) for env_vars in lambda_env_vars.values()]

    first_keys = env_var_keys_list[0]
    for idx, keys in enumerate(env_var_keys_list[1:], start=1):
        assert keys == first_keys, (
            f"Lambda function {idx} has different environment variable keys. "
            f"Expected: {sorted(first_keys)}, but found: {sorted(keys)}"
        )

    for lambda_logical_id, env_vars in lambda_env_vars.items():
        assert "SLACK_SECRET_ARN" in env_vars, (
            f"Lambda function {lambda_logical_id} is missing SLACK_SECRET_ARN"
        )

    for lambda_logical_id, env_vars in lambda_env_vars.items():
        assert "SLACK_BOT_TOKEN" not in env_vars, (
            f"Lambda function {lambda_logical_id} should not have SLACK_BOT_TOKEN"
        )
        assert "SLACK_SIGNING_SECRET" not in env_vars, (
            f"Lambda function {lambda_logical_id} should not have SLACK_SIGNING_SECRET"
        )

    secret_arn_values = [env_vars["SLACK_SECRET_ARN"] for env_vars in lambda_env_vars.values()]

    first_secret_arn = secret_arn_values[0]
    for idx, secret_arn in enumerate(secret_arn_values[1:], start=1):
        assert secret_arn == first_secret_arn, (
            f"Lambda function {idx} references a different secret ARN. "
            f"Expected: {first_secret_arn}, but found: {secret_arn}"
        )
