"""Property-based tests for IAM role construct."""

from hypothesis import given, strategies as st, settings
from aws_cdk import App, Stack
from aws_cdk.assertions import Template
from infra.custom_constructs.application_stack.iam_role import AgentIamRole


@settings(deadline=None)  # Disable deadline for this test as CDK synthesis can be slow
@given(
    memory_arn=st.text(min_size=1, max_size=100),
    parameter_namespace=st.sampled_from([
        "/bedrock-quota-agent/",
        "/custom-namespace/",
        "/test/",
    ])
)
def test_iam_role_contains_all_required_permissions(memory_arn: str, parameter_namespace: str):
    """
    Verify IAM role contains all required permissions.
    
    For any synthesized IAM role in the stack, the role's policies should include all required actions:
    - ECR image pulling (ecr:GetAuthorizationToken, ecr:BatchGetImage, ecr:GetDownloadUrlForLayer)
    - Service Quotas access (servicequotas:ListServiceQuotas, servicequotas:GetServiceQuota, 
      servicequotas:ListAWSDefaultServiceQuotas)
    - CloudWatch metrics (cloudwatch:GetMetricStatistics, cloudwatch:ListMetrics)
    - Bedrock API (bedrock:ListFoundationModels, bedrock:InvokeModel)
    - AgentCore Memory operations (bedrock-agentcore:GetMemory, bedrock-agentcore:CreateEvent,
      bedrock-agentcore:ListEvents)
    - SSM Parameter Store read access (ssm:GetParameter, ssm:GetParameters)
    """
    # Create a test stack with the IAM role construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the IAM role construct
    AgentIamRole(
        stack,
        "TestIamRole",
        memory_resource_arn=memory_arn,
        parameter_namespace=parameter_namespace
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the IAM role resource from the template
    template_dict = template.to_json()
    
    # Find the IAM role in the resources
    iam_roles = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::IAM::Role"
    }
    
    assert len(iam_roles) > 0, "No IAM role found in template"
    
    # Get the role resource (should be only one)
    role_resource = list(iam_roles.values())[0]
    
    # Extract all policy statements from inline policies
    # CDK stores inline policies in the Policies property
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
        policy_document = policy_resource.get("Properties", {}).get("PolicyDocument", {})
        statements = policy_document.get("Statement", [])
        all_statements.extend(statements)
    
    # Collect all actions from all statements
    all_actions = set()
    for statement in all_statements:
        actions = statement.get("Action", [])
        if isinstance(actions, str):
            all_actions.add(actions)
        else:
            all_actions.update(actions)
    
    # Define required permissions by category (Requirements 2.2-2.7)
    required_permissions = {
        # Requirement 2.2: ECR permissions
        "ecr:GetAuthorizationToken",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        
        # Requirement 2.3: Service Quotas permissions
        "servicequotas:ListServiceQuotas",
        "servicequotas:GetServiceQuota",
        "servicequotas:ListAWSDefaultServiceQuotas",
        
        # Requirement 2.4: CloudWatch permissions
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        
        # Requirement 2.5: Bedrock permissions
        "bedrock:ListFoundationModels",
        "bedrock:InvokeModel",
        
        # Requirement 2.6: Memory permissions (AgentCore Memory API)
        "bedrock-agentcore:GetMemory",
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
        
        # Requirement 2.7: SSM permissions
        "ssm:GetParameter",
        "ssm:GetParameters",
    }
    
    # Verify all required permissions are present
    missing_permissions = required_permissions - all_actions
    
    assert len(missing_permissions) == 0, (
        f"IAM role is missing required permissions: {missing_permissions}. "
        f"Found actions: {all_actions}"
    )
    
    # Verify that memory permissions are scoped to the specific resource
    memory_actions = {
        "bedrock-agentcore:GetMemory",
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
    }
    memory_statements = [
        stmt for stmt in all_statements
        if any(action in memory_actions
               for action in (stmt.get("Action", []) if isinstance(stmt.get("Action", []), list) 
                            else [stmt.get("Action", "")]))
    ]
    
    if memory_statements:
        for stmt in memory_statements:
            resources = stmt.get("Resource", [])
            if isinstance(resources, str):
                resources = [resources]
            
            # Memory permissions should be scoped (not "*")
            assert memory_arn in resources or any(
                (memory_arn in r if isinstance(r, str) else False) for r in resources
            ), (
                f"Memory permissions should be scoped to {memory_arn}, "
                f"but found resources: {resources}"
            )
    
    # Verify SSM permissions are scoped to the parameter namespace
    ssm_statements = [
        stmt for stmt in all_statements
        if any(action.startswith("ssm:") 
               for action in (stmt.get("Action", []) if isinstance(stmt.get("Action", []), list) 
                            else [stmt.get("Action", "")]))
    ]
    
    if ssm_statements:
        for stmt in ssm_statements:
            resources = stmt.get("Resource", [])
            if isinstance(resources, str):
                resources = [resources]
            
            # SSM permissions should be scoped to namespace
            namespace_base = parameter_namespace.rstrip('/')
            assert any(namespace_base in r for r in resources), (
                f"SSM permissions should be scoped to {parameter_namespace}, "
                f"but found resources: {resources}"
            )


@given(
    memory_arn=st.text(min_size=1, max_size=100),
    parameter_namespace=st.sampled_from([
        "/bedrock-quota-agent/",
        "/custom-namespace/",
        "/test/",
    ])
)
def test_iam_role_trust_policy_includes_required_principals(memory_arn: str, parameter_namespace: str):
    """
    Verify IAM role trust policy includes required principals.
    
    For any synthesized IAM role in the stack, the role's trust policy should allow both
    bedrock.amazonaws.com and bedrock-agentcore.amazonaws.com to assume the role.
    """
    # Create a test stack with the IAM role construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the IAM role construct
    AgentIamRole(
        stack,
        "TestIamRole",
        memory_resource_arn=memory_arn,
        parameter_namespace=parameter_namespace
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the IAM role resource from the template
    template_dict = template.to_json()
    
    # Find the IAM role in the resources
    iam_roles = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::IAM::Role"
    }
    
    assert len(iam_roles) > 0, "No IAM role found in template"
    
    # Get the role resource (should be only one)
    role_resource = list(iam_roles.values())[0]
    
    # Extract the AssumeRolePolicyDocument (trust policy)
    assume_role_policy = role_resource.get("Properties", {}).get("AssumeRolePolicyDocument", {})
    
    assert assume_role_policy, "No AssumeRolePolicyDocument found in IAM role"
    
    # Get the statements from the trust policy
    statements = assume_role_policy.get("Statement", [])
    
    assert len(statements) > 0, "No statements found in AssumeRolePolicyDocument"
    
    # Collect all principals that are allowed to assume the role
    allowed_principals = set()
    
    for statement in statements:
        # Only consider Allow statements
        if statement.get("Effect") != "Allow":
            continue
        
        # Check if the action is sts:AssumeRole
        actions = statement.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        
        if "sts:AssumeRole" not in actions:
            continue
        
        # Extract principals
        principal = statement.get("Principal", {})
        
        # Handle different principal formats
        if isinstance(principal, dict):
            # Service principals are typically in the "Service" key
            services = principal.get("Service", [])
            if isinstance(services, str):
                services = [services]
            allowed_principals.update(services)
        elif isinstance(principal, str):
            allowed_principals.add(principal)
    
    # Define required principals (Requirement 2.1)
    required_principals = {
        "bedrock.amazonaws.com",
        "bedrock-agentcore.amazonaws.com",
    }
    
    # Verify both required principals are present
    missing_principals = required_principals - allowed_principals
    
    assert len(missing_principals) == 0, (
        f"IAM role trust policy is missing required principals: {missing_principals}. "
        f"Found principals: {allowed_principals}"
    )
    
    # Verify that the trust policy only allows AssumeRole action
    for statement in statements:
        if statement.get("Effect") == "Allow":
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            
            # All actions should be sts:AssumeRole
            for action in actions:
                assert action == "sts:AssumeRole", (
                    f"Trust policy should only allow sts:AssumeRole, but found: {action}"
                )


@given(
    memory_arn=st.text(min_size=1, max_size=100),
    parameter_namespace=st.sampled_from([
        "/bedrock-quota-agent/",
        "/custom-namespace/",
        "/test/",
        "/app/config/",
        "/service/params/",
    ])
)
def test_iam_role_has_ssm_parameter_read_access(memory_arn: str, parameter_namespace: str):
    """
    Verify IAM role has SSM parameter read access.
    
    For any synthesized IAM role in the stack, the role should have ssm:GetParameter and 
    ssm:GetParameters permissions scoped to the parameter namespace.
    """
    # Create a test stack with the IAM role construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the IAM role construct
    AgentIamRole(
        stack,
        "TestIamRole",
        memory_resource_arn=memory_arn,
        parameter_namespace=parameter_namespace
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the IAM role resource from the template
    template_dict = template.to_json()
    
    # Find the IAM role in the resources
    iam_roles = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::IAM::Role"
    }
    
    assert len(iam_roles) > 0, "No IAM role found in template"
    
    # Get the role resource (should be only one)
    role_resource = list(iam_roles.values())[0]
    
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
        policy_document = policy_resource.get("Properties", {}).get("PolicyDocument", {})
        statements = policy_document.get("Statement", [])
        all_statements.extend(statements)
    
    # Find SSM-related statements
    ssm_statements = [
        stmt for stmt in all_statements
        if any(action.startswith("ssm:") 
               for action in (stmt.get("Action", []) if isinstance(stmt.get("Action", []), list) 
                            else [stmt.get("Action", "")]))
    ]
    
    # Verify SSM statements exist
    assert len(ssm_statements) > 0, "No SSM permissions found in IAM role"
    
    # Collect all SSM actions
    ssm_actions = set()
    for stmt in ssm_statements:
        actions = stmt.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        ssm_actions.update([a for a in actions if a.startswith("ssm:")])
    
    # Verify required SSM actions are present (Requirement 4.6)
    required_ssm_actions = {
        "ssm:GetParameter",
        "ssm:GetParameters",
    }
    
    missing_actions = required_ssm_actions - ssm_actions
    assert len(missing_actions) == 0, (
        f"IAM role is missing required SSM actions: {missing_actions}. "
        f"Found SSM actions: {ssm_actions}"
    )
    
    # Verify SSM permissions are scoped to the parameter namespace
    namespace_base = parameter_namespace.rstrip('/')
    
    for stmt in ssm_statements:
        resources = stmt.get("Resource", [])
        if isinstance(resources, str):
            resources = [resources]
        
        # Verify at least one resource is scoped to the namespace
        has_scoped_resource = False
        for resource in resources:
            # Resource should contain the namespace pattern
            # Expected format: arn:aws:ssm:*:*:parameter/namespace/*
            if namespace_base in resource:
                has_scoped_resource = True
                break
        
        assert has_scoped_resource, (
            f"SSM permissions should be scoped to namespace '{parameter_namespace}', "
            f"but found resources: {resources}"
        )
    
    # Verify permissions are read-only (no write/delete actions)
    write_actions = {
        "ssm:PutParameter",
        "ssm:DeleteParameter",
        "ssm:DeleteParameters",
    }
    
    forbidden_actions = ssm_actions & write_actions
    assert len(forbidden_actions) == 0, (
        f"IAM role should only have read access to SSM parameters, "
        f"but found write actions: {forbidden_actions}"
    )


@given(
    memory_arn=st.text(min_size=1, max_size=100),
    parameter_namespace=st.sampled_from([
        "/bedrock-quota-agent/",
        "/custom-namespace/",
        "/test/",
    ])
)
def test_iam_role_has_ecr_pull_access(memory_arn: str, parameter_namespace: str):
    """
    Verify IAM role has ECR pull access.

    For any synthesized IAM role in the stack, the role should have ecr:GetAuthorizationToken,
    ecr:BatchGetImage, and ecr:GetDownloadUrlForLayer permissions.
    """
    # Create a test stack with the IAM role construct
    app = App()
    stack = Stack(app, "TestStack")

    # Create the IAM role construct
    AgentIamRole(
        stack,
        "TestIamRole",
        memory_resource_arn=memory_arn,
        parameter_namespace=parameter_namespace
    )

    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)

    # Get the IAM role resource from the template
    template_dict = template.to_json()

    # Find the IAM role in the resources
    iam_roles = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::IAM::Role"
    }

    assert len(iam_roles) > 0, "No IAM role found in template"

    # Get the role resource (should be only one)
    role_resource = list(iam_roles.values())[0]

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
        policy_document = policy_resource.get("Properties", {}).get("PolicyDocument", {})
        statements = policy_document.get("Statement", [])
        all_statements.extend(statements)

    # Collect all actions from all statements
    all_actions = set()
    for statement in all_statements:
        actions = statement.get("Action", [])
        if isinstance(actions, str):
            all_actions.add(actions)
        else:
            all_actions.update(actions)

    # Define required ECR permissions (Requirement 5.7)
    required_ecr_permissions = {
        "ecr:GetAuthorizationToken",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
    }

    # Verify all required ECR permissions are present
    missing_permissions = required_ecr_permissions - all_actions

    assert len(missing_permissions) == 0, (
        f"IAM role is missing required ECR permissions: {missing_permissions}. "
        f"Found actions: {all_actions}"
    )

    # Find ECR-related statements
    ecr_statements = [
        stmt for stmt in all_statements
        if any(action.startswith("ecr:")
               for action in (stmt.get("Action", []) if isinstance(stmt.get("Action", []), list)
                            else [stmt.get("Action", "")]))
    ]

    # Verify ECR statements exist
    assert len(ecr_statements) > 0, "No ECR permissions found in IAM role"

    # Verify that ECR permissions allow Effect: Allow
    for stmt in ecr_statements:
        effect = stmt.get("Effect", "")
        assert effect == "Allow", (
            f"ECR permissions should have Effect: Allow, but found: {effect}"
        )

    # Verify permissions are not overly restrictive (should work across resources)
    # ECR permissions typically need to be broad (e.g., "*") for GetAuthorizationToken
    for stmt in ecr_statements:
        actions = stmt.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]

        # Check if this statement contains ECR actions
        ecr_actions_in_stmt = [a for a in actions if a.startswith("ecr:")]

        if ecr_actions_in_stmt:
            resources = stmt.get("Resource", [])
            if isinstance(resources, str):
                resources = [resources]

            # ECR permissions should be present (either scoped or wildcard)
            assert len(resources) > 0, (
                "ECR permissions statement should have resources defined"
            )

