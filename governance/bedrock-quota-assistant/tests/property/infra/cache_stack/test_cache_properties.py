"""Property-based tests for Cache stack and constructs."""

from hypothesis import given, strategies as st, settings
from aws_cdk import App
from aws_cdk.assertions import Template
from infra.stacks.cache_stack import CacheStack


@settings(deadline=None, max_examples=100)
@given(
    environment=st.text(
        min_size=1,
        max_size=20,
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
    ).filter(lambda x: x and x[0].isalpha() and not x.endswith("-"))  # Must start with letter, no trailing dash
)
def test_table_name_includes_environment(environment: str):
    """
    Environment-specific table naming
    
    For any valid environment string (dev, staging, prod), the Cache_Table construct
    SHALL produce a table name following the pattern `bedrock-quota-codes-{environment}`.
    """
    # Create a CDK app
    app = App()
    
    # Create the CacheStack with the environment parameter
    stack = CacheStack(
        app,
        f"TestCacheStack-{environment}",
        environment=environment
    )
    
    # Expected table name pattern
    expected_table_name = f"bedrock-quota-codes-{environment}"
    
    # Synthesize the CloudFormation template to verify the table is created correctly
    template = Template.from_stack(stack)
    template_json = template.to_json()
    
    # Find the DynamoDB table resource in the template
    resources = template_json.get("Resources", {})
    dynamodb_tables = [
        (resource_id, resource)
        for resource_id, resource in resources.items()
        if resource.get("Type") == "AWS::DynamoDB::Table"
    ]
    
    # Verify exactly one DynamoDB table exists
    assert len(dynamodb_tables) == 1, (
        f"Expected exactly 1 DynamoDB table in template, found {len(dynamodb_tables)}"
    )
    
    # Verify the table has the correct name in the CloudFormation template
    table_id, table_resource = dynamodb_tables[0]
    table_properties = table_resource.get("Properties", {})
    table_name_in_template = table_properties.get("TableName", "")
    
    assert table_name_in_template == expected_table_name, (
        f"Table name in CloudFormation template '{table_name_in_template}' does not match "
        f"expected pattern. Expected: '{expected_table_name}'"
    )


@settings(deadline=None, max_examples=100)
@given(
    schedule=st.one_of(
        # Rate-based schedules
        st.builds(
            lambda n, unit: f"rate({n} {unit})",
            n=st.integers(min_value=1, max_value=365),
            unit=st.sampled_from(["minute", "minutes", "hour", "hours", "day", "days"])
        ),
        # Cron-based schedules (simplified valid patterns)
        st.builds(
            lambda minute, hour, day, month, weekday: f"cron({minute} {hour} {day} {month} {weekday} *)",
            minute=st.sampled_from(["0", "15", "30", "45", "*"]),
            hour=st.sampled_from(["0", "6", "12", "18", "*"]),
            day=st.sampled_from(["1", "15", "*", "?"]),
            month=st.sampled_from(["1", "6", "*"]),
            weekday=st.sampled_from(["1", "3", "5", "*", "?"])
        ).filter(lambda s: not ("?" in s and s.count("?") > 1))  # Only one ? allowed
    )
)
def test_schedule_customization(schedule: str):
    """
    Schedule customization
    
    For any valid EventBridge schedule expression, the RefreshLambda construct
    SHALL configure the EventBridge rule with that schedule.
    """
    # Create a CDK app
    app = App()
    
    # Create the CacheStack with a custom schedule
    stack = CacheStack(
        app,
        "TestCacheStack-schedule",
        environment="test",
        refresh_schedule=schedule
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    template_json = template.to_json()
    
    # Find the EventBridge rule resource in the template
    resources = template_json.get("Resources", {})
    event_rules = [
        (resource_id, resource)
        for resource_id, resource in resources.items()
        if resource.get("Type") == "AWS::Events::Rule"
    ]
    
    # Verify exactly one EventBridge rule exists
    assert len(event_rules) == 1, (
        f"Expected exactly 1 EventBridge rule in template, found {len(event_rules)}"
    )
    
    # Verify the rule has the correct schedule expression
    rule_id, rule_resource = event_rules[0]
    rule_properties = rule_resource.get("Properties", {})
    schedule_expression = rule_properties.get("ScheduleExpression", "")
    
    assert schedule_expression == schedule, (
        f"EventBridge rule schedule expression '{schedule_expression}' does not match "
        f"expected schedule. Expected: '{schedule}'"
    )


@settings(deadline=None, max_examples=100)
@given(
    table_name=st.text(
        min_size=3,
        max_size=255,
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    ).filter(
        lambda x: (
            x and
            x[0].isalpha() and  # Must start with letter
            not x.endswith("-") and not x.endswith("_") and not x.endswith(".")  # No trailing special chars
        )
    )
)
def test_table_name_configuration(table_name: str):
    """
    Configurable table name
    
    For any valid table name string, the CacheTable construct SHALL create a
    DynamoDB table with that exact name.
    """
    # Create a CDK app
    app = App()
    
    # Import the CacheTable construct
    from infra.custom_constructs.cache_stack.cache_table import CacheTable
    from aws_cdk import Stack
    
    # Create a test stack
    stack = Stack(app, "TestStack")
    
    # Create the CacheTable construct with the random table name
    CacheTable(
        stack,
        "TestCacheTable",
        table_name=table_name
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    template_json = template.to_json()
    
    # Find the DynamoDB table resource in the template
    resources = template_json.get("Resources", {})
    dynamodb_tables = [
        (resource_id, resource)
        for resource_id, resource in resources.items()
        if resource.get("Type") == "AWS::DynamoDB::Table"
    ]
    
    # Verify exactly one DynamoDB table exists
    assert len(dynamodb_tables) == 1, (
        f"Expected exactly 1 DynamoDB table in template, found {len(dynamodb_tables)}"
    )
    
    # Verify the table has the exact name provided
    table_id, table_resource = dynamodb_tables[0]
    table_properties = table_resource.get("Properties", {})
    table_name_in_template = table_properties.get("TableName", "")
    
    assert table_name_in_template == table_name, (
        f"Table name in CloudFormation template '{table_name_in_template}' does not match "
        f"expected name. Expected: '{table_name}'"
    )
