"""Unit tests for IAM role construct."""

from aws_cdk import App, Stack
from aws_cdk.assertions import Template, Match
from infra.custom_constructs.application_stack.iam_role import AgentIamRole


def test_iam_role_arn_accessible():
    """
    Verify that role ARN is accessible via self.role.role_arn.
    """
    # Create a test stack with the IAM role construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the IAM role construct
    iam_role_construct = AgentIamRole(
        stack,
        "TestIamRole",
        memory_resource_arn="arn:aws:bedrock:us-east-1:123456789012:memory/test-memory",
        parameter_namespace="/bedrock-quota-agent/"
    )
    
    # Verify that the role ARN is accessible
    assert hasattr(iam_role_construct, "role"), "IAM role construct should have 'role' attribute"
    assert hasattr(iam_role_construct.role, "role_arn"), "IAM role should have 'role_arn' attribute"
    
    # Verify that role_arn is not None or empty
    role_arn = iam_role_construct.role.role_arn
    assert role_arn is not None, "Role ARN should not be None"
    assert isinstance(role_arn, str), "Role ARN should be a string"
    assert len(role_arn) > 0, "Role ARN should not be empty"


def test_add_dynamodb_permissions_method_exists():
    """
    Verify that _add_dynamodb_permissions method exists.
    """
    # Create a test stack with the IAM role construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the IAM role construct
    iam_role_construct = AgentIamRole(
        stack,
        "TestIamRole",
        memory_resource_arn="arn:aws:bedrock:us-east-1:123456789012:memory/test-memory",
        parameter_namespace="/bedrock-quota-agent/"
    )
    
    # Verify that the method exists
    assert hasattr(iam_role_construct, "_add_dynamodb_permissions"), \
        "IAM role construct should have '_add_dynamodb_permissions' method"
    assert callable(getattr(iam_role_construct, "_add_dynamodb_permissions")), \
        "_add_dynamodb_permissions should be callable"


def test_dynamodb_permissions_scoped_to_table_arn():
    """
    Verify that DynamoDB permissions are scoped to specific table ARN.
    """
    # Create a test stack with the IAM role construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Test table ARN
    test_table_arn = "arn:aws:dynamodb:us-east-1:123456789012:table/test-cache-table"
    
    # Create the IAM role construct with cache_table_arn
    AgentIamRole(
        stack,
        "TestIamRole",
        memory_resource_arn="arn:aws:bedrock:us-east-1:123456789012:memory/test-memory",
        parameter_namespace="/bedrock-quota-agent/",
        cache_table_arn=test_table_arn
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Verify that the IAM policy includes DynamoDB permissions scoped to the table ARN
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like({
            "PolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Action": Match.array_equals([
                            "dynamodb:GetItem",
                            "dynamodb:Query",
                            "dynamodb:Scan",
                        ]),
                        "Resource": test_table_arn,
                    })
                ])
            })
        })
    )


def test_dynamodb_permissions_include_required_actions():
    """
    Verify that DynamoDB permissions include GetItem, Query, Scan actions.
    """
    # Create a test stack with the IAM role construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Test table ARN
    test_table_arn = "arn:aws:dynamodb:us-east-1:123456789012:table/test-cache-table"
    
    # Create the IAM role construct with cache_table_arn
    AgentIamRole(
        stack,
        "TestIamRole",
        memory_resource_arn="arn:aws:bedrock:us-east-1:123456789012:memory/test-memory",
        parameter_namespace="/bedrock-quota-agent/",
        cache_table_arn=test_table_arn
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Verify that all required DynamoDB actions are present
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like({
            "PolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Action": Match.array_with([
                            "dynamodb:GetItem",
                            "dynamodb:Query",
                            "dynamodb:Scan",
                        ]),
                        "Resource": test_table_arn,
                    })
                ])
            })
        })
    )


def test_iam_role_without_cache_table_arn():
    """
    Verify that IAM role works without cache_table_arn (backward compatibility).
    """
    # Create a test stack with the IAM role construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the IAM role construct without cache_table_arn
    AgentIamRole(
        stack,
        "TestIamRole",
        memory_resource_arn="arn:aws:bedrock:us-east-1:123456789012:memory/test-memory",
        parameter_namespace="/bedrock-quota-agent/"
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Verify that the role is created successfully
    template.resource_count_is("AWS::IAM::Role", 1)
    
    # Verify that no DynamoDB permissions are added
    # We check that there's no policy statement with DynamoDB actions
    policies = template.find_resources("AWS::IAM::Policy")
    
    # Check that none of the policies contain DynamoDB permissions
    for policy_id, policy in policies.items():
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        for statement in statements:
            actions = statement.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            # Ensure no DynamoDB actions are present
            dynamodb_actions = [a for a in actions if a.startswith("dynamodb:")]
            assert len(dynamodb_actions) == 0, \
                "No DynamoDB permissions should be present when cache_table_arn is not provided"
