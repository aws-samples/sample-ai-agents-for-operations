"""Unit tests for CacheTable construct."""

from aws_cdk import App, Stack
from aws_cdk.assertions import Template
from infra.custom_constructs.cache_stack.cache_table import CacheTable


def test_table_billing_mode():
    """
    Verify that table uses PAY_PER_REQUEST billing mode.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    CacheTable(
        stack,
        "TestCacheTable",
        table_name="test-table",
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "BillingMode": "PAY_PER_REQUEST"
    })


def test_table_keys():
    """
    Verify that table has PK (String) partition key and SK (String) sort key.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    CacheTable(
        stack,
        "TestCacheTable",
        table_name="test-table",
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "KeySchema": [
            {
                "AttributeName": "PK",
                "KeyType": "HASH"
            },
            {
                "AttributeName": "SK",
                "KeyType": "RANGE"
            }
        ],
        "AttributeDefinitions": [
            {
                "AttributeName": "PK",
                "AttributeType": "S"
            },
            {
                "AttributeName": "SK",
                "AttributeType": "S"
            }
        ]
    })


def test_table_ttl():
    """
    Verify that table enables TTL on 'ttl' attribute.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    CacheTable(
        stack,
        "TestCacheTable",
        table_name="test-table",
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "TimeToLiveSpecification": {
            "AttributeName": "ttl",
            "Enabled": True
        }
    })


def test_table_naming():
    """
    Verify that table name matches provided parameter.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    table_name = "bedrock-quota-codes-dev"
    
    CacheTable(
        stack,
        "TestCacheTable",
        table_name=table_name,
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "TableName": table_name
    })


def test_table_naming_with_different_environments():
    """
    Verify that table naming works with different environment strings.
    """
    environments = ["dev", "staging", "prod", "test"]
    
    for env in environments:
        app = App()
        stack = Stack(app, f"TestStack-{env}")
        
        table_name = f"bedrock-quota-codes-{env}"
        
        CacheTable(
            stack,
            "TestCacheTable",
            table_name=table_name,
        )
        
        template = Template.from_stack(stack)
        
        template.has_resource_properties("AWS::DynamoDB::Table", {
            "TableName": table_name
        })


def test_table_removal_policy():
    """
    Verify that table has RETAIN removal policy for production safety.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    CacheTable(
        stack,
        "TestCacheTable",
        table_name="test-table",
    )
    
    template = Template.from_stack(stack)
    
    template.has_resource("AWS::DynamoDB::Table", {
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain"
    })


def test_cloudformation_template_synthesis():
    """
    Verify that CacheTable construct synthesizes to valid CloudFormation template.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    CacheTable(
        stack,
        "TestCacheTable",
        table_name="test-table",
    )
    
    template = Template.from_stack(stack)
    
    # Verify that exactly one DynamoDB table resource is created
    template.resource_count_is("AWS::DynamoDB::Table", 1)


def test_table_property_accessible():
    """
    Verify that table property is accessible for cross-stack references.
    """
    app = App()
    stack = Stack(app, "TestStack")
    
    cache_table = CacheTable(
        stack,
        "TestCacheTable",
        table_name="test-table",
    )
    
    # Verify that the table property is accessible
    assert hasattr(cache_table, "table"), "CacheTable construct should have 'table' attribute"
    assert cache_table.table is not None, "Table should not be None"
    
    # Verify that table has expected properties
    assert hasattr(cache_table.table, "table_name"), "Table should have 'table_name' attribute"
    assert hasattr(cache_table.table, "table_arn"), "Table should have 'table_arn' attribute"
