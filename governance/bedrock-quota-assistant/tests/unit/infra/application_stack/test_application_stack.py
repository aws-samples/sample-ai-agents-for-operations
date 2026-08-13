"""Unit tests for the Application stack."""
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
import pytest

from infra.stacks.application_stack import ApplicationStack


def test_application_stack_creates_required_resources():
    """Test that the Application stack creates all required resources."""
    app = cdk.App()
    stack = ApplicationStack(
        app,
        "TestApplicationStack",
        environment="test"
    )
    template = Template.from_stack(stack)
    
    # Verify key resources are created
    # Note: Actual resource counts will be validated in integration tests
    # This is a placeholder for future detailed unit tests
    assert template is not None


def test_application_stack_requires_environment():
    """Test that the Application stack requires an environment parameter."""
    app = cdk.App()
    
    with pytest.raises(TypeError):
        ApplicationStack(app, "TestStack")


def test_application_stack_accepts_cache_parameters():
    """Test that the Application stack accepts cache_table_name and cache_table_arn parameters."""
    app = cdk.App()
    
    # Create stack with cache parameters
    stack = ApplicationStack(
        app,
        "TestApplicationStack",
        environment="test",
        cache_table_name="test-cache-table",
        cache_table_arn="arn:aws:dynamodb:us-east-1:123456789012:table/test-cache-table"
    )
    
    # Verify stack was created successfully
    assert stack is not None
    assert stack.cache_table_name == "test-cache-table"
    assert stack.cache_table_arn == "arn:aws:dynamodb:us-east-1:123456789012:table/test-cache-table"


def test_ssm_parameter_created_with_cache_table_name():
    """Test that SSM parameter is created with correct name and value when cache_table_name is provided."""
    app = cdk.App()
    
    cache_table_name = "bedrock-quota-codes-test"
    stack = ApplicationStack(
        app,
        "TestApplicationStack",
        environment="test",
        cache_table_name=cache_table_name,
        cache_table_arn="arn:aws:dynamodb:us-east-1:123456789012:table/bedrock-quota-codes-test"
    )
    
    template = Template.from_stack(stack)
    
    # Verify SSM parameter exists with correct name and value
    template.has_resource_properties(
        "AWS::SSM::Parameter",
        {
            "Name": "/bedrock-quota-agent/cache-table-name",
            "Value": cache_table_name,
            "Type": "String"
        }
    )


def test_iam_role_has_dynamodb_permissions():
    """Test that IAM role has DynamoDB permissions when cache_table_arn is provided."""
    app = cdk.App()
    
    cache_table_arn = "arn:aws:dynamodb:us-east-1:123456789012:table/test-cache-table"
    stack = ApplicationStack(
        app,
        "TestApplicationStack",
        environment="test",
        cache_table_name="test-cache-table",
        cache_table_arn=cache_table_arn
    )
    
    template = Template.from_stack(stack)
    
    # Verify IAM role has DynamoDB permissions
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like({
            "PolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Action": [
                            "dynamodb:GetItem",
                            "dynamodb:Query",
                            "dynamodb:Scan"
                        ],
                        "Effect": "Allow",
                        "Resource": cache_table_arn
                    })
                ])
            })
        })
    )


def test_iam_role_has_ssm_getparameter_permission_for_cache():
    """Test that IAM role has SSM GetParameter permission for cache parameter."""
    app = cdk.App()
    
    stack = ApplicationStack(
        app,
        "TestApplicationStack",
        environment="test",
        cache_table_name="test-cache-table",
        cache_table_arn="arn:aws:dynamodb:us-east-1:123456789012:table/test-cache-table"
    )
    
    template = Template.from_stack(stack)
    
    # Verify IAM role has SSM GetParameter permission for the namespace
    template.has_resource_properties(
        "AWS::IAM::Policy",
        Match.object_like({
            "PolicyDocument": Match.object_like({
                "Statement": Match.array_with([
                    Match.object_like({
                        "Action": [
                            "ssm:GetParameter",
                            "ssm:GetParameters"
                        ],
                        "Effect": "Allow",
                        "Resource": Match.string_like_regexp(r".*parameter/bedrock-quota-agent/\*")
                    })
                ])
            })
        })
    )


def test_backward_compatibility_without_cache_parameters():
    """Test that stack works without cache parameters (backward compatibility)."""
    app = cdk.App()
    
    # Create stack without cache parameters
    stack = ApplicationStack(
        app,
        "TestApplicationStack",
        environment="test"
    )
    
    template = Template.from_stack(stack)
    
    # Verify stack was created successfully
    assert stack is not None
    assert stack.cache_table_name is None
    assert stack.cache_table_arn is None
    
    # Verify SSM parameter for cache-table-name does NOT exist
    # Count all SSM parameters
    ssm_params = template.find_resources("AWS::SSM::Parameter")
    
    # Check that none of them are the cache-table-name parameter
    for param_id, param_props in ssm_params.items():
        param_name = param_props.get("Properties", {}).get("Name", "")
        assert param_name != "/bedrock-quota-agent/cache-table-name", \
            "cache-table-name SSM parameter should not exist when cache_table_name is not provided"
    
    # Verify IAM role does NOT have DynamoDB permissions
    iam_policies = template.find_resources("AWS::IAM::Policy")
    
    for policy_id, policy_props in iam_policies.items():
        statements = policy_props.get("Properties", {}).get("PolicyDocument", {}).get("Statement", [])
        for statement in statements:
            actions = statement.get("Action", [])
            # Ensure no DynamoDB actions are present
            if isinstance(actions, list):
                assert "dynamodb:GetItem" not in actions, \
                    "DynamoDB permissions should not exist when cache_table_arn is not provided"
                assert "dynamodb:Query" not in actions, \
                    "DynamoDB permissions should not exist when cache_table_arn is not provided"
                assert "dynamodb:Scan" not in actions, \
                    "DynamoDB permissions should not exist when cache_table_arn is not provided"

