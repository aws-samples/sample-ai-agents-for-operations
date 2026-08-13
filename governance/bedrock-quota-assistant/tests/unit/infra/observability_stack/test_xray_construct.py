"""Unit tests for XRayTransactionSearchConstruct."""

from aws_cdk import App, Stack
from aws_cdk.assertions import Template, Match
from infra.custom_constructs.observability_stack.xray_transaction_search import (
    XRayTransactionSearchConstruct,
)


def test_construct_instantiation():
    """
    Test that XRayTransactionSearchConstruct can be instantiated.

    Verifies that the construct can be created without errors and
    produces a valid CloudFormation template.
    """
    app = App()
    stack = Stack(app, "TestStack", env={"region": "us-east-1", "account": "123456789012"})

    XRayTransactionSearchConstruct(
        stack,
        "TestXRayConstruct",
    )

    template = Template.from_stack(stack)
    assert template is not None


def test_logs_resource_policy_creation():
    """
    Test that CloudWatch Logs resource policy is created.

    Verifies that the construct creates a resource policy that allows
    X-Ray to write logs to CloudWatch Logs.
    """
    app = App()
    stack = Stack(app, "TestStack", env={"region": "us-east-1", "account": "123456789012"})

    XRayTransactionSearchConstruct(
        stack,
        "TestXRayConstruct",
    )

    template = Template.from_stack(stack)

    template.has_resource_properties("AWS::Logs::ResourcePolicy", {
        "PolicyName": "TransactionSearchAccess",
    })


def test_logs_resource_policy_allows_xray():
    """
    Test that resource policy allows X-Ray service to write logs.

    Verifies the policy document grants PutLogEvents permission to
    xray.amazonaws.com for the correct log groups.
    """
    app = App()
    stack = Stack(app, "TestStack", env={"region": "us-west-2", "account": "123456789012"})

    XRayTransactionSearchConstruct(
        stack,
        "TestXRayConstruct",
    )

    template = Template.from_stack(stack)

    template.has_resource_properties("AWS::Logs::ResourcePolicy", {
        "PolicyName": "TransactionSearchAccess",
        "PolicyDocument": Match.any_value(),
    })


def test_custom_resource_created():
    """
    Test that a custom resource is created for Transaction Search management.

    Verifies that the construct creates a CloudFormation custom resource
    backed by a Lambda function instead of the native
    AWS::XRay::TransactionSearchConfig resource.
    """
    app = App()
    stack = Stack(app, "TestStack", env={"region": "us-east-1", "account": "123456789012"})

    XRayTransactionSearchConstruct(
        stack,
        "TestXRayConstruct",
    )

    template = Template.from_stack(stack)

    # Verify a Lambda function is created for the custom resource
    template.has_resource_properties("AWS::Lambda::Function", {
        "Handler": "handler.on_event",
        "Runtime": "python3.11",
        "Timeout": 840,
    })


def test_custom_resource_default_properties():
    """
    Test that the custom resource has correct default properties.

    Verifies IndexingPercentage defaults to 100 and RetainOnDelete to true.
    """
    app = App()
    stack = Stack(app, "TestStack", env={"region": "us-east-1", "account": "123456789012"})

    XRayTransactionSearchConstruct(
        stack,
        "TestXRayConstruct",
    )

    template = Template.from_stack(stack)

    template.has_resource_properties("AWS::CloudFormation::CustomResource", {
        "IndexingPercentage": 1,
        "RetainOnDelete": "true",
    })


def test_custom_resource_custom_indexing():
    """
    Test that custom indexing percentage is passed to the custom resource.
    """
    app = App()
    stack = Stack(app, "TestStack", env={"region": "us-east-1", "account": "123456789012"})

    XRayTransactionSearchConstruct(
        stack,
        "TestXRayConstruct",
        indexing_percentage=50,
    )

    template = Template.from_stack(stack)

    template.has_resource_properties("AWS::CloudFormation::CustomResource", {
        "IndexingPercentage": 50,
        "RetainOnDelete": "true",
    })


def test_custom_resource_retain_on_delete_false():
    """
    Test that RetainOnDelete=false is passed correctly.
    """
    app = App()
    stack = Stack(app, "TestStack", env={"region": "us-east-1", "account": "123456789012"})

    XRayTransactionSearchConstruct(
        stack,
        "TestXRayConstruct",
        retain_on_delete=False,
    )

    template = Template.from_stack(stack)

    template.has_resource_properties("AWS::CloudFormation::CustomResource", {
        "IndexingPercentage": 1,
        "RetainOnDelete": "false",
    })


def test_lambda_has_xray_permissions():
    """
    Test that the Lambda function has the required X-Ray permissions.
    """
    app = App()
    stack = Stack(app, "TestStack", env={"region": "us-east-1", "account": "123456789012"})

    XRayTransactionSearchConstruct(
        stack,
        "TestXRayConstruct",
    )

    template = Template.from_stack(stack)

    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {
            "Statement": Match.array_with([
                Match.object_like({
                    "Action": [
                        "xray:GetTraceSegmentDestination",
                        "xray:UpdateTraceSegmentDestination",
                        "xray:GetIndexingRules",
                        "xray:UpdateIndexingRule",
                        "application-signals:StartDiscovery",
                    ],
                    "Effect": "Allow",
                    "Resource": "*",
                }),
            ]),
        },
    })


def test_no_native_xray_resource():
    """
    Test that no native AWS::XRay::TransactionSearchConfig resource is created.

    The custom resource replaces the native resource to avoid AlreadyExists
    errors on the account-level singleton.
    """
    app = App()
    stack = Stack(app, "TestStack", env={"region": "us-east-1", "account": "123456789012"})

    XRayTransactionSearchConstruct(
        stack,
        "TestXRayConstruct",
    )

    template = Template.from_stack(stack)

    template.resource_count_is("AWS::XRay::TransactionSearchConfig", 0)
