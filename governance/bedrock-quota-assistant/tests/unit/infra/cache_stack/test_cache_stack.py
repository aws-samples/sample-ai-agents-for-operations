"""Unit tests for CacheStack."""

from aws_cdk import App
from aws_cdk.assertions import Match, Template
from infra.stacks.cache_stack import CacheStack


def test_stack_synthesis():
    """
    Verify that CacheStack synthesizes to valid CloudFormation template.
    """
    app = App()
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
    )
    
    template = Template.from_stack(stack)
    
    # Verify that key resources are created
    template.resource_count_is("AWS::DynamoDB::Table", 1)
    # AsyncCachePopulator creates 2 Lambda functions: the refresh Lambda and the custom resource provider
    template.resource_count_is("AWS::Lambda::Function", 2)
    template.resource_count_is("AWS::Events::Rule", 1)
    template.resource_count_is("Custom::AWS", 1)  # AsyncCachePopulator custom resource


def test_stack_naming_pattern():
    """
    Verify that stack name follows the expected pattern.
    """
    app = App()
    
    environments = ["dev", "staging", "prod"]
    
    for env in environments:
        stack = CacheStack(
            app,
            f"BedrockQuotaAgent-{env}-Cache",
            environment=env,
        )
        
        # Verify stack was created successfully
        assert stack.stack_name == f"BedrockQuotaAgent-{env}-Cache"


def test_stack_exposes_table_property():
    """
    Verify that stack exposes table property for cross-stack references.
    """
    app = App()
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
    )
    
    # Verify that table property is accessible
    assert hasattr(stack, "table"), "CacheStack should have 'table' property"
    assert stack.table is not None, "Table property should not be None"
    
    # Verify that table has expected attributes
    assert hasattr(stack.table, "table_name"), "Table should have 'table_name' attribute"
    assert hasattr(stack.table, "table_arn"), "Table should have 'table_arn' attribute"


def test_stack_exposes_table_name_property():
    """
    Verify that stack exposes table_name property for convenience.
    """
    app = App()
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
    )
    
    # Verify that table_name property is accessible
    assert hasattr(stack, "table_name"), "CacheStack should have 'table_name' property"
    assert stack.table_name is not None, "Table name should not be None"
    
    # Verify table name is a string (may be a CDK token)
    assert isinstance(stack.table_name, str), "Table name should be a string"


def test_stack_exposes_table_arn_property():
    """
    Verify that stack exposes table_arn property for convenience.
    """
    app = App()
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
    )
    
    # Verify that table_arn property is accessible
    assert hasattr(stack, "table_arn"), "CacheStack should have 'table_arn' property"
    assert stack.table_arn is not None, "Table ARN should not be None"


def test_stack_applies_project_tag():
    """
    Verify that stack applies Project tag to all resources.
    """
    app = App()
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
    )
    
    template = Template.from_stack(stack)
    
    # Verify that DynamoDB table has Project tag
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "Tags": Match.array_with([
            {
                "Key": "Project",
                "Value": "BedrockQuotaAgent"
            }
        ])
    })


def test_stack_applies_environment_tag():
    """
    Verify that stack applies Environment tag to all resources.
    """
    environments = ["dev", "staging", "prod"]
    
    for env in environments:
        # Create a new App for each test to avoid synthesis conflicts
        app = App()
        
        stack = CacheStack(
            app,
            f"TestCacheStack-{env}",
            environment=env,
        )
        
        template = Template.from_stack(stack)
        
        # Verify that DynamoDB table has Environment tag
        template.has_resource_properties("AWS::DynamoDB::Table", {
            "Tags": Match.array_with([
                {
                    "Key": "Environment",
                    "Value": env
                }
            ])
        })


def test_stack_creates_table_name_output():
    """
    Verify that stack creates CloudFormation output for table name.
    """
    app = App()
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
    )
    
    template = Template.from_stack(stack)
    
    # Verify that TableName output exists
    template.has_output("TableName", {
        "Description": "Name of the cache DynamoDB table",
        "Export": {
            "Name": "TestCacheStack-TableName"
        }
    })


def test_stack_creates_table_arn_output():
    """
    Verify that stack creates CloudFormation output for table ARN.
    """
    app = App()
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
    )
    
    template = Template.from_stack(stack)
    
    # Verify that TableArn output exists
    template.has_output("TableArn", {
        "Description": "ARN of the cache DynamoDB table",
        "Export": {
            "Name": "TestCacheStack-TableArn"
        }
    })


def test_stack_creates_lambda_function_arn_output():
    """
    Verify that stack creates CloudFormation output for Lambda function ARN.
    """
    app = App()
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
    )
    
    template = Template.from_stack(stack)
    
    # Verify that LambdaFunctionArn output exists
    template.has_output("LambdaFunctionArn", {
        "Description": "ARN of the refresh Lambda function",
        "Export": {
            "Name": "TestCacheStack-LambdaFunctionArn"
        }
    })


def test_stack_accepts_custom_refresh_schedule():
    """
    Verify that stack accepts custom refresh_schedule parameter.
    """
    app = App()
    
    custom_schedule = "rate(1 day)"
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
        refresh_schedule=custom_schedule,
    )
    
    template = Template.from_stack(stack)
    
    # Verify that EventBridge rule uses custom schedule
    template.has_resource_properties("AWS::Events::Rule", {
        "ScheduleExpression": custom_schedule,
        "State": "ENABLED"
    })


def test_stack_uses_default_refresh_schedule():
    """
    Verify that stack uses default refresh_schedule when not provided.
    """
    app = App()
    
    stack = CacheStack(
        app,
        "TestCacheStack",
        environment="dev",
    )
    
    template = Template.from_stack(stack)
    
    # Verify that EventBridge rule uses default schedule
    template.has_resource_properties("AWS::Events::Rule", {
        "ScheduleExpression": "rate(7 days)",
        "State": "ENABLED"
    })


def test_stack_validates_environment_parameter():
    """
    Verify that stack validates environment parameter.
    """
    app = App()
    
    # Test with empty string
    try:
        CacheStack(
            app,
            "TestCacheStack1",
            environment="",
        )
        assert False, "Should have raised ValueError for empty environment"
    except ValueError as e:
        assert "environment parameter must be a non-empty string" in str(e)
    
    # Test with None
    try:
        CacheStack(
            app,
            "TestCacheStack2",
            environment=None,
        )
        assert False, "Should have raised ValueError for None environment"
    except ValueError as e:
        assert "environment parameter must be a non-empty string" in str(e)


def test_stack_table_name_includes_environment():
    """
    Verify that table name includes environment parameter.
    """
    environments = ["dev", "staging", "prod", "test"]
    
    for env in environments:
        # Create a new App for each test to avoid synthesis conflicts
        app = App()
        
        stack = CacheStack(
            app,
            f"TestCacheStack-{env}",
            environment=env,
        )
        
        template = Template.from_stack(stack)
        
        # Verify that table name includes environment
        template.has_resource_properties("AWS::DynamoDB::Table", {
            "TableName": f"bedrock-quota-codes-{env}"
        })
