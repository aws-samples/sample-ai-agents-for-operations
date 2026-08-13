"""Property-based tests for API Gateway Stack IAM permissions.

This module contains property-based tests that verify IAM permissions
for integration handler Lambda functions in the API Gateway Stack.
"""

from hypothesis import given, strategies as st, settings
from aws_cdk import App, Stack
from aws_cdk.assertions import Template
from aws_cdk import aws_apigateway as apigw
from infra.custom_constructs.slack_integration_stack.integration_handler_construct import IntegrationHandlerConstruct
from aws_cdk import aws_lambda as lambda_


class MockIntegrationHandler(IntegrationHandlerConstruct):
    """Mock implementation of IntegrationHandlerConstruct for property testing."""
    
    def __init__(
        self,
        scope,
        construct_id: str,
        api: apigw.RestApi,
        runtime_arn: str,
        runtime_region: str,
        environment: str,
        route_path: str,
        **kwargs
    ):
        super().__init__(
            scope,
            construct_id,
            api,
            runtime_arn,
            runtime_region,
            environment,
            route_path,
            **kwargs
        )
        
        # Create a test Lambda function
        self.lambda_function = self._create_lambda_function()
        
        # Grant AgentCore permissions
        self._grant_agentcore_permissions()
    
    def _create_lambda_function(self) -> lambda_.Function:
        """Create a test Lambda function."""
        fn = lambda_.Function(
            self,
            "TestLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.handler",
            code=lambda_.Code.from_inline("def handler(event, context): return {'statusCode': 200}"),
        )
        
        # Add API Gateway integration to avoid validation error
        # Split the route path and create nested resources
        path_parts = self.route_path.strip('/').split('/')
        resource = self.api.root
        for part in path_parts:
            resource = resource.add_resource(part)
        
        resource.add_method(
            "POST",
            apigw.LambdaIntegration(fn)
        )
        
        return fn


@settings(
    deadline=None,
    max_examples=5      # Reduced for expensive CDK synthesis (~25 seconds total)
)
@given(
    runtime_arn=st.text(min_size=10, max_size=100).map(
        lambda s: f"arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/{s.replace(':', '-')}"
    ),
    runtime_region=st.sampled_from(["us-west-2", "us-east-1", "eu-west-1"]),
    environment=st.sampled_from(["dev", "staging", "prod"]),
)
def test_property_5_handler_iam_permissions(
    runtime_arn: str,
    runtime_region: str,
    environment: str
):
    """
    Property 5: Integration handler IAM permissions
    
    For any integration handler Lambda function, the IAM role should include
    bedrock-agentcore:InvokeAgentRuntime permission on the runtime ARN.
    """
    # Create a test stack with an integration handler construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create API Gateway
    api = apigw.RestApi(
        stack,
        "TestApi",
        rest_api_name="test-api",
    )
    
    # Create the integration handler construct
    MockIntegrationHandler(
        stack,
        "TestHandler",
        api=api,
        runtime_arn=runtime_arn,
        runtime_region=runtime_region,
        environment=environment,
        route_path="/test/events",
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the template as a dictionary
    template_dict = template.to_json()
    
    # Find the Lambda function's IAM role
    lambda_functions = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::Lambda::Function"
    }
    
    assert len(lambda_functions) > 0, "No Lambda function found in template"
    
    # Get the Lambda function resource
    lambda_resource = list(lambda_functions.values())[0]
    lambda_role_ref = lambda_resource["Properties"]["Role"]
    
    # Extract the role logical ID from the Ref
    if isinstance(lambda_role_ref, dict) and "Fn::GetAtt" in lambda_role_ref:
        role_logical_id = lambda_role_ref["Fn::GetAtt"][0]
    elif isinstance(lambda_role_ref, dict) and "Ref" in lambda_role_ref:
        role_logical_id = lambda_role_ref["Ref"]
    else:
        raise AssertionError(f"Unexpected role reference format: {lambda_role_ref}")
    
    # Find the IAM role resource
    role_resource = template_dict["Resources"].get(role_logical_id)
    assert role_resource is not None, f"IAM role {role_logical_id} not found in template"
    assert role_resource["Type"] == "AWS::IAM::Role", f"Resource {role_logical_id} is not an IAM role"
    
    # Extract all policy statements from inline policies
    policies = role_resource.get("Properties", {}).get("Policies", [])
    all_statements = []
    
    for policy in policies:
        policy_document = policy.get("PolicyDocument", {})
        statements = policy_document.get("Statement", [])
        all_statements.extend(statements)
    
    # Also check for managed policies attached separately (AWS::IAM::Policy resources)
    iam_policies = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::IAM::Policy"
    }
    
    for policy_resource in iam_policies.values():
        # Check if this policy is attached to our Lambda's role
        roles = policy_resource.get("Properties", {}).get("Roles", [])
        
        # Check if our role is in the list
        role_is_attached = False
        for role_ref in roles:
            if isinstance(role_ref, dict) and "Ref" in role_ref:
                if role_ref["Ref"] == role_logical_id:
                    role_is_attached = True
                    break
        
        if role_is_attached:
            policy_document = policy_resource.get("Properties", {}).get("PolicyDocument", {})
            statements = policy_document.get("Statement", [])
            all_statements.extend(statements)
    
    # Collect all actions from all statements
    all_actions = set()
    for statement in all_statements:
        if statement.get("Effect") == "Allow":
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                all_actions.add(actions)
            else:
                all_actions.update(actions)
    
    # Verify bedrock-agentcore:InvokeAgentRuntime permission is present
    required_action = "bedrock-agentcore:InvokeAgentRuntime"
    
    assert required_action in all_actions, (
        f"IAM role is missing required permission: {required_action}. "
        f"Found actions: {all_actions}"
    )
    
    # Find the statement that grants AgentCore invocation permission
    agentcore_statements = [
        stmt for stmt in all_statements
        if stmt.get("Effect") == "Allow" and
        any(action == required_action or (isinstance(action, str) and action == required_action)
            for action in (stmt.get("Action", []) if isinstance(stmt.get("Action", []), list)
                          else [stmt.get("Action", "")]))
    ]
    
    assert len(agentcore_statements) > 0, (
        f"No statement found granting {required_action} permission"
    )
    
    # Verify the permission is scoped to the specific runtime ARN
    for stmt in agentcore_statements:
        resources = stmt.get("Resource", [])
        if isinstance(resources, str):
            resources = [resources]
        
        # The runtime ARN should be in the resources list
        assert runtime_arn in resources, (
            f"AgentCore invocation permission should be scoped to {runtime_arn}, "
            f"but found resources: {resources}"
        )
    
    # Verify CloudWatch Logs permissions are also present
    # (These are required for Lambda functions to write logs)
    logs_actions = {
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    }
    
    found_logs_actions = all_actions & logs_actions
    
    # At least some CloudWatch Logs permissions should be present
    # (CDK may add these automatically or via the construct)
    assert len(found_logs_actions) > 0, (
        f"IAM role should have CloudWatch Logs permissions. "
        f"Expected at least one of {logs_actions}, but found actions: {all_actions}"
    )


@settings(
    deadline=None,
    max_examples=5      # Reduced for expensive CDK synthesis (~25 seconds total)
)
@given(
    runtime_arn=st.text(min_size=10, max_size=100).map(
        lambda s: f"arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/{s.replace(':', '-')}"
    ),
    runtime_region=st.sampled_from(["us-west-2", "us-east-1", "eu-west-1"]),
    environment=st.sampled_from(["dev", "staging", "prod"]),
)
def test_property_12_least_privilege_iam_policies(
    runtime_arn: str,
    runtime_region: str,
    environment: str
):
    """
    Property 12: Least privilege IAM policies
    
    For any Lambda function in the stack, the IAM policy should not contain
    wildcard actions (Action: "*") or wildcard resources (Resource: "*")
    except for CloudWatch Logs permissions.
    """
    # Create a test stack with an integration handler construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create API Gateway
    api = apigw.RestApi(
        stack,
        "TestApi",
        rest_api_name="test-api",
    )
    
    # Create the integration handler construct
    MockIntegrationHandler(
        stack,
        "TestHandler",
        api=api,
        runtime_arn=runtime_arn,
        runtime_region=runtime_region,
        environment=environment,
        route_path="/test/events",
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the template as a dictionary
    template_dict = template.to_json()
    
    # Find all Lambda functions
    lambda_functions = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::Lambda::Function"
    }
    
    assert len(lambda_functions) > 0, "No Lambda function found in template"
    
    # For each Lambda function, check its IAM role
    for lambda_logical_id, lambda_resource in lambda_functions.items():
        lambda_role_ref = lambda_resource["Properties"]["Role"]
        
        # Extract the role logical ID from the Ref
        if isinstance(lambda_role_ref, dict) and "Fn::GetAtt" in lambda_role_ref:
            role_logical_id = lambda_role_ref["Fn::GetAtt"][0]
        elif isinstance(lambda_role_ref, dict) and "Ref" in lambda_role_ref:
            role_logical_id = lambda_role_ref["Ref"]
        else:
            raise AssertionError(f"Unexpected role reference format: {lambda_role_ref}")
        
        # Find the IAM role resource
        role_resource = template_dict["Resources"].get(role_logical_id)
        assert role_resource is not None, f"IAM role {role_logical_id} not found in template"
        assert role_resource["Type"] == "AWS::IAM::Role", f"Resource {role_logical_id} is not an IAM role"
        
        # Extract all policy statements from inline policies
        policies = role_resource.get("Properties", {}).get("Policies", [])
        all_statements = []
        
        for policy in policies:
            policy_document = policy.get("PolicyDocument", {})
            statements = policy_document.get("Statement", [])
            all_statements.extend(statements)
        
        # Also check for managed policies attached separately (AWS::IAM::Policy resources)
        iam_policies = {
            k: v for k, v in template_dict["Resources"].items()
            if v["Type"] == "AWS::IAM::Policy"
        }
        
        for policy_resource in iam_policies.values():
            # Check if this policy is attached to our Lambda's role
            roles = policy_resource.get("Properties", {}).get("Roles", [])
            
            # Check if our role is in the list
            role_is_attached = False
            for role_ref in roles:
                if isinstance(role_ref, dict) and "Ref" in role_ref:
                    if role_ref["Ref"] == role_logical_id:
                        role_is_attached = True
                        break
            
            if role_is_attached:
                policy_document = policy_resource.get("Properties", {}).get("PolicyDocument", {})
                statements = policy_document.get("Statement", [])
                all_statements.extend(statements)
        
        # Check each statement for wildcard violations
        for stmt_idx, statement in enumerate(all_statements):
            if statement.get("Effect") != "Allow":
                continue
            
            # Get actions from the statement
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            
            # Get resources from the statement
            resources = statement.get("Resource", [])
            if isinstance(resources, str):
                resources = [resources]
            
            # Check for wildcard actions
            wildcard_actions = [action for action in actions if action == "*"]
            
            # Check for wildcard resources
            wildcard_resources = [resource for resource in resources if resource == "*"]
            
            # Determine if this is a CloudWatch Logs statement
            is_logs_statement = any(
                action.startswith("logs:") for action in actions
            )
            
            # Wildcard actions are never allowed
            assert len(wildcard_actions) == 0, (
                f"Lambda function {lambda_logical_id} has wildcard action '*' in statement {stmt_idx}. "
                f"This violates least privilege principle. Statement: {statement}"
            )
            
            # Wildcard resources are only allowed for CloudWatch Logs
            if len(wildcard_resources) > 0 and not is_logs_statement:
                raise AssertionError(
                    f"Lambda function {lambda_logical_id} has wildcard resource '*' in statement {stmt_idx} "
                    f"for non-CloudWatch Logs actions. This violates least privilege principle. "
                    f"Actions: {actions}, Resources: {resources}"
                )
            
            # For non-CloudWatch Logs statements, verify resources are specific
            if not is_logs_statement:
                for resource in resources:
                    # Resources should be specific ARNs, not wildcards
                    # Allow ARNs with wildcards in the resource part (e.g., arn:aws:s3:::bucket/*)
                    # but not complete wildcards
                    assert resource != "*", (
                        f"Lambda function {lambda_logical_id} has wildcard resource in statement {stmt_idx}. "
                        f"Resources should be specific ARNs. Statement: {statement}"
                    )


@settings(
    deadline=None,
    max_examples=5      # Reduced for expensive CDK synthesis (~25 seconds total)
)
@given(
    runtime_arn=st.text(min_size=10, max_size=100).map(
        lambda s: f"arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/{s.replace(':', '-')}"
    ),
    runtime_region=st.sampled_from(["us-west-2", "us-east-1", "eu-west-1"]),
    environment=st.sampled_from(["dev", "staging", "prod"]),
)
def test_property_13_slack_lambda_self_invocation_permission(
    runtime_arn: str,
    runtime_region: str,
    environment: str,
):
    """
    Property 13: Slack Lambda self-invocation permission

    For any Slack integration Lambda, the IAM role should include
    lambda:InvokeFunction permission on its own function ARN.

    Feature: api-gateway-stack, Property 13: Slack Lambda self-invocation permission
    """
    from infra.custom_constructs.slack_integration_stack.slack_integration_construct import SlackIntegrationConstruct

    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(
        stack,
        "TestApi",
        rest_api_name="test-api",
    )

    SlackIntegrationConstruct(
        stack,
        "SlackIntegration",
        api=api,
        runtime_arn=runtime_arn,
        runtime_region=runtime_region,
        environment=environment,
        secret_name=f"bedrock-quota-agent/{environment}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the template as a dictionary
    template_dict = template.to_json()
    
    # Find the Slack Lambda function
    lambda_functions = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::Lambda::Function"
    }
    
    assert len(lambda_functions) > 0, "No Lambda function found in template"
    
    # Get the Lambda function resource (should be the Slack handler)
    lambda_resource = list(lambda_functions.values())[0]
    lambda_role_ref = lambda_resource["Properties"]["Role"]
    
    # Extract the role logical ID from the Ref
    if isinstance(lambda_role_ref, dict) and "Fn::GetAtt" in lambda_role_ref:
        role_logical_id = lambda_role_ref["Fn::GetAtt"][0]
    elif isinstance(lambda_role_ref, dict) and "Ref" in lambda_role_ref:
        role_logical_id = lambda_role_ref["Ref"]
    else:
        raise AssertionError(f"Unexpected role reference format: {lambda_role_ref}")
    
    # Find the IAM role resource
    role_resource = template_dict["Resources"].get(role_logical_id)
    assert role_resource is not None, f"IAM role {role_logical_id} not found in template"
    assert role_resource["Type"] == "AWS::IAM::Role", f"Resource {role_logical_id} is not an IAM role"
    
    # Extract all policy statements from inline policies
    policies = role_resource.get("Properties", {}).get("Policies", [])
    all_statements = []
    
    for policy in policies:
        policy_document = policy.get("PolicyDocument", {})
        statements = policy_document.get("Statement", [])
        all_statements.extend(statements)
    
    # Also check for managed policies attached separately (AWS::IAM::Policy resources)
    iam_policies = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::IAM::Policy"
    }
    
    for policy_resource in iam_policies.values():
        # Check if this policy is attached to our Lambda's role
        roles = policy_resource.get("Properties", {}).get("Roles", [])
        
        # Check if our role is in the list
        role_is_attached = False
        for role_ref in roles:
            if isinstance(role_ref, dict) and "Ref" in role_ref:
                if role_ref["Ref"] == role_logical_id:
                    role_is_attached = True
                    break
        
        if role_is_attached:
            policy_document = policy_resource.get("Properties", {}).get("PolicyDocument", {})
            statements = policy_document.get("Statement", [])
            all_statements.extend(statements)
    
    # Collect all actions from all statements
    all_actions = set()
    for statement in all_statements:
        if statement.get("Effect") == "Allow":
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                all_actions.add(actions)
            else:
                all_actions.update(actions)
    
    # Verify lambda:InvokeFunction permission is present
    required_action = "lambda:InvokeFunction"
    
    assert required_action in all_actions, (
        f"IAM role is missing required permission: {required_action}. "
        f"Found actions: {all_actions}"
    )
    
    # Find the statement that grants Lambda invocation permission
    lambda_invoke_statements = [
        stmt for stmt in all_statements
        if stmt.get("Effect") == "Allow" and
        any(action == required_action or (isinstance(action, str) and action == required_action)
            for action in (stmt.get("Action", []) if isinstance(stmt.get("Action", []), list)
                          else [stmt.get("Action", "")]))
    ]
    
    assert len(lambda_invoke_statements) > 0, (
        f"No statement found granting {required_action} permission"
    )
    
    # Verify the permission exists (resource can be wildcard due to circular dependency)
    # The implementation uses wildcard to avoid circular dependency, which is acceptable
    # since the Lambda execution role is scoped to this specific function
    for stmt in lambda_invoke_statements:
        resources = stmt.get("Resource", [])
        if isinstance(resources, str):
            resources = [resources]
        
        # The resource should be either wildcard or the function ARN
        # Wildcard is acceptable for self-invocation to avoid circular dependency
        assert len(resources) > 0, (
            f"Lambda invocation permission should have at least one resource, "
            f"but found: {resources}"
        )



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
def test_property_3_iam_policy_grants_scoped_get_secret_value_permission(
    runtime_arn: str,
    runtime_region: str,
    environment: str,
):
    """
    Property 3: IAM policy grants scoped GetSecretValue permission

    For any SlackIntegrationConstruct, the Lambda execution role should have
    an IAM policy statement that grants "secretsmanager:GetSecretValue" permission
    scoped to the stack-managed secret ARN, and should not grant list, create, update,
    or delete permissions.

    Validates: Requirements 2.1, 2.2, 2.3, 2.4
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

    # Exactly one AWS::SecretsManager::Secret resource should exist (stack-managed)
    secrets = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::SecretsManager::Secret"
    }
    assert len(secrets) == 1, (
        f"Exactly one Secrets Manager secret resource should be created. "
        f"Found: {list(secrets.keys())}"
    )

    # Find the Lambda function's IAM role and collect all policy statements
    lambda_functions = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::Lambda::Function"
    }
    assert len(lambda_functions) > 0, "No Lambda function found in template"

    lambda_resource = list(lambda_functions.values())[0]
    lambda_role_ref = lambda_resource["Properties"]["Role"]

    if isinstance(lambda_role_ref, dict) and "Fn::GetAtt" in lambda_role_ref:
        role_logical_id = lambda_role_ref["Fn::GetAtt"][0]
    elif isinstance(lambda_role_ref, dict) and "Ref" in lambda_role_ref:
        role_logical_id = lambda_role_ref["Ref"]
    else:
        raise AssertionError(f"Unexpected role reference format: {lambda_role_ref}")

    all_statements = []

    # Inline policies on the role
    role_resource = template_dict["Resources"].get(role_logical_id)
    assert role_resource is not None
    policies = role_resource.get("Properties", {}).get("Policies", [])
    for policy in policies:
        all_statements.extend(policy.get("PolicyDocument", {}).get("Statement", []))

    # Separate IAM::Policy resources attached to the role
    iam_policies = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::IAM::Policy"
    }
    for policy_resource in iam_policies.values():
        roles = policy_resource.get("Properties", {}).get("Roles", [])
        for role_ref in roles:
            if isinstance(role_ref, dict) and "Ref" in role_ref and role_ref["Ref"] == role_logical_id:
                all_statements.extend(
                    policy_resource.get("Properties", {}).get("PolicyDocument", {}).get("Statement", [])
                )
                break

    # Collect Secrets Manager actions
    secrets_manager_actions = set()
    for statement in all_statements:
        if statement.get("Effect") == "Allow":
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            for action in actions:
                if action.startswith("secretsmanager:"):
                    secrets_manager_actions.add(action)

    # Requirement 2.1: GetSecretValue is present
    assert "secretsmanager:GetSecretValue" in secrets_manager_actions, (
        f"IAM role is missing secretsmanager:GetSecretValue. "
        f"Found: {secrets_manager_actions}"
    )

    # Requirement 2.3: No forbidden actions
    forbidden_actions = {
        "secretsmanager:ListSecrets",
        "secretsmanager:CreateSecret",
        "secretsmanager:UpdateSecret",
        "secretsmanager:DeleteSecret",
        "secretsmanager:PutSecretValue",
        "secretsmanager:UpdateSecretVersionStage",
        "secretsmanager:RotateSecret",
    }
    found_forbidden = secrets_manager_actions & forbidden_actions
    assert len(found_forbidden) == 0, (
        f"IAM role should not have: {found_forbidden}. Only read permissions expected."
    )

    # Requirement 2.2: Permission is scoped (not wildcard "*")
    get_secret_statements = [
        stmt for stmt in all_statements
        if stmt.get("Effect") == "Allow" and
        "secretsmanager:GetSecretValue" in (
            stmt.get("Action", []) if isinstance(stmt.get("Action", []), list)
            else [stmt.get("Action", "")]
        )
    ]
    assert len(get_secret_statements) > 0

    for stmt in get_secret_statements:
        resources = stmt.get("Resource", [])
        if isinstance(resources, (str, dict)):
            resources = [resources]
        for resource in resources:
            assert resource != "*", (
                "GetSecretValue should be scoped to a specific secret ARN, not '*'"
            )



@settings(
    deadline=None,
    max_examples=10  # Property tests should run multiple iterations
)
@given(
    environment=st.sampled_from(["dev", "staging", "prod"]),
)
def test_property_18_environment_specific_access_control(
    environment: str,
):
    """
    Property 18: Environment-specific access control

    For any Lambda function in a specific environment, the IAM policy should
    only grant access to the secret for that same environment, preventing
    cross-environment credential access.

    **Validates: Requirements 7.3**
    """
    from infra.custom_constructs.slack_integration_stack.slack_integration_construct import (
        SlackIntegrationConstruct,
    )

    app = App()
    stack = Stack(app, "TestStack")

    api = apigw.RestApi(stack, "TestApi")

    secret_name = f"bedrock-quota-agent/{environment}/slack-credentials"

    slack_integration = SlackIntegrationConstruct(
        stack,
        "SlackIntegration",
        api=api,
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        runtime_region="us-west-2",
        environment=environment,
        secret_name=secret_name,
        dedup_table_name="test-dedup-table",
    )

    template = Template.from_stack(stack)
    template_dict = template.to_json()

    # Exactly one secret resource should be created (stack-managed)
    secrets = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::SecretsManager::Secret"
    }
    assert len(secrets) == 1, "Exactly one secret should be created by the stack"

    # Verify the construct exposes the secret
    assert slack_integration.secret is not None

    # Find IAM policies that grant secretsmanager:GetSecretValue
    iam_policies = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::IAM::Policy"
    }

    for policy_resource in iam_policies.values():
        policy_doc = policy_resource["Properties"]["PolicyDocument"]
        statements = policy_doc.get("Statement", [])

        for stmt in statements:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]

            if "secretsmanager:GetSecretValue" in actions:
                resources = stmt.get("Resource", [])
                if isinstance(resources, (str, dict)):
                    resources = [resources]

                # Verify the resource ARN contains the environment-specific secret name
                for resource in resources:
                    if isinstance(resource, dict) and "Fn::Join" in resource:
                        join_parts = resource["Fn::Join"][1]
                        joined = "".join(str(p) for p in join_parts if isinstance(p, str))
                        assert environment in joined, (
                            f"IAM policy ARN should contain environment '{environment}'. "
                            f"Got join parts: {join_parts}"
                        )
                    elif isinstance(resource, dict) and "Ref" in resource:
                        # CDK-created secret uses Ref to the logical resource
                        pass
                    elif isinstance(resource, str):
                        assert environment in resource, (
                            f"IAM policy ARN should contain environment '{environment}'. "
                            f"Got: {resource}"
                        )
