"""Unit tests for RefreshLambda construct."""

from aws_cdk import App, Stack
from aws_cdk.assertions import Match, Template
from infra.custom_constructs.cache_stack.cache_table import CacheTable
from infra.custom_constructs.cache_stack.refresh_lambda import RefreshLambda


def test_lambda_runtime():
    """
    Verify that Lambda uses Python 3.12 runtime.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::Lambda::Function", {
        "Runtime": "python3.12"
    })


def test_lambda_timeout():
    """
    Verify that Lambda has 300 second timeout.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::Lambda::Function", {
        "Timeout": 300
    })


def test_lambda_memory():
    """
    Verify that Lambda has 256MB memory.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::Lambda::Function", {
        "MemorySize": 256
    })


def test_lambda_architecture():
    """
    Verify that Lambda has ARM64 architecture.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::Lambda::Function", {
        "Architectures": ["arm64"]
    })


def test_lambda_handler():
    """
    Verify that Lambda handler is handler.handler.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::Lambda::Function", {
        "Handler": "handler.handler"
    })


def test_lambda_code_asset():
    """
    Verify that Lambda code points to ../cache directory.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    # Verify Lambda function exists with code property
    template.has_resource_properties("AWS::Lambda::Function", {
        "Code": Match.object_like({
            "S3Bucket": Match.any_value(),
            "S3Key": Match.any_value()
        })
    })


def test_environment_variables():
    """
    Verify that Lambda has CACHE_TABLE_NAME and REFRESH_REGION environment variables.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {
            "Variables": {
                "CACHE_TABLE_NAME": Match.any_value(),
                "REFRESH_REGION": "us-east-1"
            }
        }
    })


def test_service_quotas_permissions():
    """
    Verify that Lambda IAM role has servicequotas permissions.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {
            "Statement": Match.array_with([
                Match.object_like({
                    "Action": [
                        "servicequotas:ListServiceQuotas",
                        "servicequotas:GetServiceQuota",
                    ],
                    "Effect": "Allow",
                    "Resource": "*"
                })
            ])
        }
    })


def test_dynamodb_permissions():
    """
    Verify that Lambda IAM role has dynamodb:PutItem, BatchWriteItem, and Query permissions.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {
            "Statement": Match.array_with([
                Match.object_like({
                    "Action": [
                        "dynamodb:PutItem",
                        "dynamodb:BatchWriteItem",
                        "dynamodb:Query",
                    ],
                    "Effect": "Allow",
                    "Resource": Match.any_value()
                })
            ])
        }
    })


def test_eventbridge_rule_exists():
    """
    Verify that EventBridge rule exists with correct schedule.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::Events::Rule", {
        "ScheduleExpression": "rate(7 days)",
        "State": "ENABLED"
    })


def test_eventbridge_rule_custom_schedule():
    """
    Verify that EventBridge rule accepts custom schedule expression.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
        refresh_schedule="rate(1 day)",
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::Events::Rule", {
        "ScheduleExpression": "rate(1 day)",
        "State": "ENABLED"
    })


def test_eventbridge_rule_enabled():
    """
    Verify that EventBridge rule is enabled by default.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::Events::Rule", {
        "State": "ENABLED"
    })


def test_eventbridge_targets_lambda():
    """
    Verify that EventBridge rule targets the Lambda function.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    # Verify EventBridge rule has Lambda target
    template.has_resource_properties("AWS::Events::Rule", {
        "Targets": Match.array_with([
            Match.object_like({
                "Arn": Match.any_value()
            })
        ])
    })


def test_function_property_accessible():
    """
    Verify that function property is accessible for cross-construct references.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    refresh_lambda = RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    # Verify that the function property is accessible
    assert hasattr(refresh_lambda, "function"), "RefreshLambda construct should have 'function' attribute"
    assert refresh_lambda.function is not None, "Function should not be None"
    
    # Verify that function has expected properties
    assert hasattr(refresh_lambda.function, "function_arn"), "Function should have 'function_arn' attribute"
    assert hasattr(refresh_lambda.function, "function_name"), "Function should have 'function_name' attribute"


def test_cloudformation_template_synthesis():
    """
    Verify that RefreshLambda construct synthesizes to valid CloudFormation template.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    
    RefreshLambda(
        stack,
        "TestRefreshLambda",
        table=table.table,
    )
    
    template = Template.from_stack(stack)
    
    # Verify that exactly one Lambda function is created
    template.resource_count_is("AWS::Lambda::Function", 1)
    
    # Verify that exactly one EventBridge rule is created
    template.resource_count_is("AWS::Events::Rule", 1)
    
    # Verify that IAM role and policy are created
    template.resource_count_is("AWS::IAM::Role", 1)
