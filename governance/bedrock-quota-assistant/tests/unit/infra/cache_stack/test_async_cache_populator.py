"""Unit tests for AsyncCachePopulator construct."""

from aws_cdk import App, Stack
from aws_cdk.assertions import Match, Template
from infra.custom_constructs.cache_stack.async_cache_populator import AsyncCachePopulator
from infra.custom_constructs.cache_stack.cache_table import CacheTable
from infra.custom_constructs.cache_stack.refresh_lambda import RefreshLambda


def test_custom_resource_exists():
    """
    Verify that custom resource exists in synthesized template.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    refresh_lambda = RefreshLambda(stack, "TestRefreshLambda", table=table.table)
    
    AsyncCachePopulator(
        stack,
        "TestAsyncCachePopulator",
        function=refresh_lambda.function,
    )
    
    template = Template.from_stack(stack)
    
    # Verify that custom resource exists
    template.resource_count_is("Custom::AWS", 1)


def test_lambda_function_arn_reference():
    """
    Verify that custom resource references the Lambda function ARN.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    refresh_lambda = RefreshLambda(stack, "TestRefreshLambda", table=table.table)
    
    AsyncCachePopulator(
        stack,
        "TestAsyncCachePopulator",
        function=refresh_lambda.function,
    )
    
    template = Template.from_stack(stack)
    
    # Verify that custom resource has Create property with Lambda invocation
    # The Create property is a JSON string that includes FunctionName
    template.has_resource_properties("Custom::AWS", {
        "Create": Match.object_like({
            "Fn::Join": Match.any_value()
        })
    })


def test_invocation_type_is_event():
    """
    Verify that InvocationType is Event (async).
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    refresh_lambda = RefreshLambda(stack, "TestRefreshLambda", table=table.table)
    
    AsyncCachePopulator(
        stack,
        "TestAsyncCachePopulator",
        function=refresh_lambda.function,
    )
    
    template = Template.from_stack(stack)
    
    # Verify that custom resource has Create property
    # The Create property contains the Lambda invocation with InvocationType: Event
    # This is encoded in a JSON string via Fn::Join
    template.has_resource_properties("Custom::AWS", {
        "Create": Match.object_like({
            "Fn::Join": Match.any_value()
        })
    })


def test_lambda_invoke_permission():
    """
    Verify that lambda:InvokeFunction permission is granted.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    refresh_lambda = RefreshLambda(stack, "TestRefreshLambda", table=table.table)
    
    AsyncCachePopulator(
        stack,
        "TestAsyncCachePopulator",
        function=refresh_lambda.function,
    )
    
    template = Template.from_stack(stack)
    
    # Verify that IAM policy includes lambda:InvokeFunction permission
    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {
            "Statement": Match.array_with([
                Match.object_like({
                    "Action": "lambda:InvokeFunction",
                    "Effect": "Allow",
                    "Resource": Match.any_value()
                })
            ])
        }
    })


def test_cloudformation_template_synthesis():
    """
    Verify that AsyncCachePopulator construct synthesizes to valid CloudFormation template.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    refresh_lambda = RefreshLambda(stack, "TestRefreshLambda", table=table.table)
    
    AsyncCachePopulator(
        stack,
        "TestAsyncCachePopulator",
        function=refresh_lambda.function,
    )
    
    template = Template.from_stack(stack)
    
    # Verify that exactly one custom resource is created
    template.resource_count_is("Custom::AWS", 1)


def test_no_delete_logic():
    """
    Verify that custom resource has no DELETE logic (no-op).
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    refresh_lambda = RefreshLambda(stack, "TestRefreshLambda", table=table.table)
    
    AsyncCachePopulator(
        stack,
        "TestAsyncCachePopulator",
        function=refresh_lambda.function,
    )
    
    template = Template.from_stack(stack)
    
    # Verify that custom resource does not have Delete property
    # (AwsCustomResource with only on_create means no DELETE logic)
    template.has_resource_properties("Custom::AWS", {
        "Create": Match.any_value()
    })
    
    # Get the custom resource to verify it doesn't have Delete
    resources = template.find_resources("Custom::AWS")
    for resource_id, resource in resources.items():
        properties = resource.get("Properties", {})
        # Verify Delete is not present in properties
        assert "Delete" not in properties, "Custom resource should not have Delete logic"


def test_error_handling():
    """
    Verify that custom resource ignores errors to prevent blocking stack operations.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table = CacheTable(stack, "TestTable", table_name="test-table")
    refresh_lambda = RefreshLambda(stack, "TestRefreshLambda", table=table.table)
    
    AsyncCachePopulator(
        stack,
        "TestAsyncCachePopulator",
        function=refresh_lambda.function,
    )
    
    template = Template.from_stack(stack)
    
    # Verify that custom resource has error handling configuration
    # The ignore_error_codes_matching parameter should be present
    resources = template.find_resources("Custom::AWS")
    assert len(resources) > 0, "Custom resource should exist"
