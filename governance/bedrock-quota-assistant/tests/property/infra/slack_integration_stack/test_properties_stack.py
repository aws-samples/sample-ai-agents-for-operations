"""Property-based tests for API Gateway Stack.

This module contains property-based tests that verify API Gateway Stack
behavior across different configurations and inputs.
"""

from hypothesis import given, strategies as st, settings, Phase
from aws_cdk import App
from aws_cdk.assertions import Template
from infra.stacks.slack_integration_stack import SlackIntegrationStack


# Strategy for generating valid AgentCore runtime ARNs
@st.composite
def agentcore_runtime_arn(draw):
    """Generate a valid AgentCore runtime ARN for testing.

    Returns a string representing a valid AWS ARN for an AgentCore runtime.
    Format: arn:aws:bedrock-agentcore:region:account:runtime/runtime-id
    """
    # Generate region (common AWS regions)
    region = draw(st.sampled_from([
        "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"
    ]))

    # Generate account ID (12 digits)
    account_id = draw(st.integers(min_value=100000000000, max_value=999999999999))

    # Generate runtime ID (alphanumeric string)
    runtime_id = draw(st.text(
        min_size=8,
        max_size=64,
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), blacklist_characters='-')
    ))

    return f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{runtime_id}"


@settings(
    deadline=None,  # CDK synthesis can be slow
    max_examples=2  # Minimal coverage for expensive CDK tests
)
@given(runtime_arn=agentcore_runtime_arn())
def test_stack_accepts_runtime_arn_parameter(runtime_arn):
    """
    Test that stack accepts any valid AgentCore runtime ARN.

    For any valid AgentCore runtime ARN string, instantiating SlackIntegrationStack
    with that ARN should succeed without error. This ensures the stack can
    reference external AgentCore runtimes deployed in different accounts or regions.
    """
    app = App()

    # Stack should instantiate successfully with any valid runtime ARN
    stack = SlackIntegrationStack(
        app,
        "TestSlackIntegrationStack",
        environment="dev",
        runtime_arn=runtime_arn,
        slack_secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Verify stack was created successfully
    assert stack is not None, "Stack instantiation failed"

    # Verify the runtime ARN was stored correctly
    assert stack.runtime_arn == runtime_arn, (
        f"Stack did not store runtime ARN correctly. "
        f"Expected: {runtime_arn}, Got: {stack.runtime_arn}"
    )

    # Verify the stack can be synthesized to CloudFormation
    template = Template.from_stack(stack)

    # Verify the template is valid (has resources)
    resources = template.to_json().get('Resources', {})
    assert len(resources) > 0, (
        "Stack template has no resources - synthesis may have failed"
    )

    # Verify the Lambda function references the runtime ARN in environment variables
    lambda_functions = {
        logical_id: resource
        for logical_id, resource in resources.items()
        if resource.get('Type') == 'AWS::Lambda::Function'
    }

    assert len(lambda_functions) > 0, (
        "Stack should create at least one Lambda function"
    )

    # Check that at least one Lambda function has the runtime ARN in its environment
    found_runtime_arn = False
    for logical_id, function in lambda_functions.items():
        env_vars = function.get('Properties', {}).get('Environment', {}).get('Variables', {})
        if 'AGENTCORE_ARN' in env_vars:
            # The ARN might be a reference or the actual value
            agentcore_arn_value = env_vars['AGENTCORE_ARN']
            if isinstance(agentcore_arn_value, str) and agentcore_arn_value == runtime_arn:
                found_runtime_arn = True
                break

    assert found_runtime_arn, (
        "Lambda function environment variables should include the runtime ARN"
    )


@settings(
    deadline=None,  # CDK synthesis can be slow
    max_examples=2  # Minimal coverage for expensive CDK tests
)
@given(runtime_arn=agentcore_runtime_arn())
def test_stack_independence_from_application_stack(runtime_arn):
    """
    Test that SlackIntegrationStack can be deployed independently from ApplicationStack.

    For any external runtime ARN, synthesizing SlackIntegrationStack should produce a valid
    CloudFormation template with no dependencies on ApplicationStack resources. This
    ensures the API Gateway Stack can be deployed to different accounts or regions
    without requiring ApplicationStack to be present.

    Feature: api-gateway-stack, Property 2: Stack independence from ApplicationStack
    Validates: Requirements 1.3
    """
    app = App()

    # Create SlackIntegrationStack with external runtime ARN (no ApplicationStack)
    stack = SlackIntegrationStack(
        app,
        "IndependentSlackIntegrationStack",
        environment="dev",
        runtime_arn=runtime_arn,
        slack_secret_name="bedrock-quota-agent/dev/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Synthesize the stack to CloudFormation template
    template = Template.from_stack(stack)
    template_json = template.to_json()

    # Verify the template is valid
    assert 'Resources' in template_json, (
        "Template should contain Resources section"
    )

    resources = template_json['Resources']
    assert len(resources) > 0, (
        "Template should contain at least one resource"
    )

    # Verify no dependencies on ApplicationStack resources
    # Check that no resources reference ApplicationStack exports
    for logical_id, resource in resources.items():
        properties = resource.get('Properties', {})

        # Check for Fn::ImportValue references to ApplicationStack
        def check_for_application_stack_imports(obj, path=""):
            """Recursively check for ApplicationStack imports in the object."""
            if isinstance(obj, dict):
                # Check for Fn::ImportValue
                if 'Fn::ImportValue' in obj:
                    import_value = obj['Fn::ImportValue']
                    # Check if it references ApplicationStack
                    if isinstance(import_value, str):
                        assert 'ApplicationStack' not in import_value, (
                            f"Resource {logical_id} at {path} imports from ApplicationStack: {import_value}. "
                            f"SlackIntegrationStack should be independent and not reference ApplicationStack exports."
                        )
                    elif isinstance(import_value, dict):
                        # Could be a complex reference
                        import_str = str(import_value)
                        assert 'ApplicationStack' not in import_str, (
                            f"Resource {logical_id} at {path} imports from ApplicationStack: {import_str}. "
                            f"SlackIntegrationStack should be independent and not reference ApplicationStack exports."
                        )

                # Recursively check nested objects
                for key, value in obj.items():
                    check_for_application_stack_imports(value, f"{path}.{key}")

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    check_for_application_stack_imports(item, f"{path}[{i}]")

        check_for_application_stack_imports(properties, f"Resources.{logical_id}.Properties")

    # Verify the stack uses the external runtime ARN directly
    # Find Lambda functions and check they use the provided ARN
    lambda_functions = {
        logical_id: resource
        for logical_id, resource in resources.items()
        if resource.get('Type') == 'AWS::Lambda::Function'
    }

    assert len(lambda_functions) > 0, (
        "Stack should create at least one Lambda function"
    )

    # Verify Lambda functions use the external runtime ARN
    for logical_id, function in lambda_functions.items():
        env_vars = function.get('Properties', {}).get('Environment', {}).get('Variables', {})
        if 'AGENTCORE_ARN' in env_vars:
            agentcore_arn_value = env_vars['AGENTCORE_ARN']
            # Should be the direct ARN value, not a reference
            assert isinstance(agentcore_arn_value, str), (
                f"Lambda {logical_id} should use direct ARN value, not a reference"
            )
            assert agentcore_arn_value == runtime_arn, (
                f"Lambda {logical_id} should use the provided external runtime ARN"
            )

    # Verify no DependsOn references to ApplicationStack resources
    for logical_id, resource in resources.items():
        depends_on = resource.get('DependsOn', [])
        if isinstance(depends_on, str):
            depends_on = [depends_on]

        for dependency in depends_on:
            assert 'ApplicationStack' not in dependency, (
                f"Resource {logical_id} has DependsOn reference to ApplicationStack resource: {dependency}. "
                f"SlackIntegrationStack should be independent."
            )

    # Verify the template can be deployed independently
    # Check that all required parameters are provided (no missing cross-stack references)
    parameters = template_json.get('Parameters', {})

    # SlackIntegrationStack should not require parameters from ApplicationStack
    for param_name, param_config in parameters.items():
        assert 'ApplicationStack' not in param_name, (
            f"Template has parameter {param_name} that references ApplicationStack. "
            f"SlackIntegrationStack should be independent."
        )


@settings(
    deadline=None,  # CDK synthesis can be slow
    max_examples=2  # Minimal coverage for expensive CDK tests
)
@given(
    environment=st.sampled_from(["dev", "staging", "prod"]),
    runtime_arn=agentcore_runtime_arn()
)
def test_environment_specific_naming(environment, runtime_arn):
    """
    Test that all resources include environment in their logical IDs or names.

    For any environment value (dev, staging, prod), all resources created by the stack
    should include the environment in their logical IDs or names. This ensures proper
    resource isolation and identification across different deployment environments.

    Feature: api-gateway-stack, Property 14: Environment-specific naming
    Validates: Requirements 7.2
    """
    app = App()

    # Create stack with the given environment
    stack = SlackIntegrationStack(
        app,
        f"TestSlackIntegrationStack-{environment}",
        environment=environment,
        runtime_arn=runtime_arn,
        slack_secret_name=f"bedrock-quota-agent/{environment}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Synthesize the stack to CloudFormation template
    template = Template.from_stack(stack)
    template_json = template.to_json()

    # Get all resources from the template
    resources = template_json.get('Resources', {})
    assert len(resources) > 0, "Stack should create at least one resource"

    # Verify each resource includes environment in logical ID or resource properties
    for logical_id, resource in resources.items():
        resource_type = resource.get('Type', '')
        properties = resource.get('Properties', {})

        # Check if environment is in the logical ID (case-insensitive)
        has_env_in_logical_id = environment.lower() in logical_id.lower()

        # Check if environment is in resource-specific name properties
        has_env_in_name = False

        # Check common name properties based on resource type
        if resource_type == 'AWS::Lambda::Function':
            function_name = properties.get('FunctionName', '')
            if isinstance(function_name, str):
                has_env_in_name = environment.lower() in function_name.lower()

        elif resource_type == 'AWS::ApiGateway::RestApi':
            api_name = properties.get('Name', '')
            if isinstance(api_name, str):
                has_env_in_name = environment.lower() in api_name.lower()

        elif resource_type == 'AWS::ApiGateway::Stage':
            stage_name = properties.get('StageName', '')
            if isinstance(stage_name, str):
                has_env_in_name = environment.lower() in stage_name.lower()

        elif resource_type == 'AWS::Lambda::LayerVersion':
            layer_name = properties.get('LayerName', '')
            if isinstance(layer_name, str):
                has_env_in_name = environment.lower() in layer_name.lower()

        elif resource_type == 'AWS::IAM::Role':
            role_name = properties.get('RoleName', '')
            if isinstance(role_name, str):
                has_env_in_name = environment.lower() in role_name.lower()

        elif resource_type == 'AWS::Logs::LogGroup':
            log_group_name = properties.get('LogGroupName', '')
            if isinstance(log_group_name, str):
                has_env_in_name = environment.lower() in log_group_name.lower()

        # For CloudFormation outputs, check the logical ID and export name
        elif resource_type == 'AWS::CloudFormation::Output':
            # Outputs are in a separate section, not in Resources
            pass

        # Assert that environment appears in either logical ID or name property
        assert has_env_in_logical_id or has_env_in_name, (
            f"Resource {logical_id} (type: {resource_type}) does not include "
            f"environment '{environment}' in its logical ID or name properties. "
            f"Logical ID: {logical_id}, Properties: {properties}"
        )

    # Also verify CloudFormation outputs include environment
    outputs = template_json.get('Outputs', {})
    for output_id, output_config in outputs.items():
        # Check if environment is in the output logical ID
        has_env_in_output_id = environment.lower() in output_id.lower()

        # Check if environment is in the export name
        export_name = output_config.get('Export', {}).get('Name', '')
        has_env_in_export = False
        if isinstance(export_name, str):
            has_env_in_export = environment.lower() in export_name.lower()

        # At least one should contain the environment
        # Note: Export names follow {StackName}-{OutputKey} pattern,
        # so if stack name has environment, export name will too
        assert has_env_in_output_id or has_env_in_export, (
            f"Output {output_id} does not include environment '{environment}' "
            f"in its logical ID or export name. Export name: {export_name}"
        )


@settings(
    deadline=None,  # CDK synthesis can be slow
    max_examples=2,  # Minimal coverage for expensive CDK tests
    phases=[Phase.generate]  # Skip shrinking during active development
)
@given(
    environment=st.sampled_from(["dev", "staging", "prod"]),
    runtime_arn=agentcore_runtime_arn()
)
def test_environment_tagging(environment, runtime_arn):
    """
    Test that all resources have Environment tag with the correct value.

    For any environment value, all resources in the synthesized CloudFormation template
    should have an Environment tag with that value. This ensures proper resource
    organization, cost tracking, and environment identification across AWS accounts.
    """
    app = App()

    # Create stack with the given environment
    stack = SlackIntegrationStack(
        app,
        f"TestSlackIntegrationStack-{environment}",
        environment=environment,
        runtime_arn=runtime_arn,
        slack_secret_name=f"bedrock-quota-agent/{environment}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Synthesize the stack to CloudFormation template
    template = Template.from_stack(stack)
    template_json = template.to_json()

    # Get all resources from the template
    resources = template_json.get('Resources', {})
    assert len(resources) > 0, "Stack should create at least one resource"

    # Track resources that should be tagged
    # Some AWS resources don't support tags (like IAM policies, log groups in some cases)
    taggable_resource_types = {
        'AWS::Lambda::Function',
        'AWS::ApiGateway::RestApi',
        'AWS::ApiGateway::Stage',
        'AWS::IAM::Role',
    }

    # Resources that typically don't support tags or have different tagging mechanisms
    non_taggable_resource_types = {
        'AWS::Lambda::Permission',
        'AWS::ApiGateway::Resource',
        'AWS::ApiGateway::Method',
        'AWS::ApiGateway::Deployment',
        'AWS::IAM::Policy',
        'AWS::Logs::LogGroup',  # Log groups use different tagging
        'AWS::Lambda::LayerVersion',  # Lambda layers don't support tags in CloudFormation
    }

    # Verify each taggable resource has the Environment tag
    for logical_id, resource in resources.items():
        resource_type = resource.get('Type', '')
        properties = resource.get('Properties', {})

        # Skip non-taggable resources
        if resource_type in non_taggable_resource_types:
            continue

        # For taggable resources, verify Environment tag exists
        if resource_type in taggable_resource_types:
            tags = properties.get('Tags', [])

            # Tags can be in different formats depending on the resource type
            # Most resources use array format: [{"Key": "...", "Value": "..."}]
            # Some use object format: {"Key": "Value"}

            environment_tag_found = False
            environment_tag_value = None

            if isinstance(tags, list):
                # Array format (most common)
                for tag in tags:
                    if isinstance(tag, dict):
                        tag_key = tag.get('Key', '')
                        tag_value = tag.get('Value', '')
                        if tag_key == 'Environment':
                            environment_tag_found = True
                            environment_tag_value = tag_value
                            break

            elif isinstance(tags, dict):
                # Object format (less common)
                if 'Environment' in tags:
                    environment_tag_found = True
                    environment_tag_value = tags['Environment']

            # Assert that Environment tag exists
            assert environment_tag_found, (
                f"Resource {logical_id} (type: {resource_type}) does not have an "
                f"Environment tag. All taggable resources should be tagged with the "
                f"deployment environment. Tags found: {tags}"
            )

            # Assert that Environment tag has the correct value
            assert environment_tag_value == environment, (
                f"Resource {logical_id} (type: {resource_type}) has Environment tag "
                f"with incorrect value. Expected: '{environment}', Got: '{environment_tag_value}'"
            )

    # Verify that at least some resources were checked
    # (to ensure the test isn't passing vacuously)
    taggable_resources = [
        logical_id for logical_id, resource in resources.items()
        if resource.get('Type', '') in taggable_resource_types
    ]

    assert len(taggable_resources) > 0, (
        f"Stack should create at least one taggable resource. "
        f"Found resource types: {[r.get('Type') for r in resources.values()]}"
    )


@settings(
    deadline=None,  # CDK synthesis can be slow
    max_examples=2,  # Minimal coverage for expensive CDK tests
    phases=[Phase.generate]  # Skip shrinking during active development
)
@given(
    env1=st.sampled_from(["dev", "staging", "prod"]),
    env2=st.sampled_from(["dev", "staging", "prod"]),
    runtime_arn=agentcore_runtime_arn()
)
def test_environment_isolation(env1, env2, runtime_arn):
    """
    Test that different environments produce non-conflicting resource names.

    For any two different environment values, synthesizing two stacks with those
    environments should produce resources with non-conflicting names. This ensures
    that multiple environments can be deployed to the same AWS account without
    resource name collisions.

    Feature: api-gateway-stack, Property 16: Environment isolation
    Validates: Requirements 7.5
    """
    # Skip test if environments are the same (no isolation needed)
    if env1 == env2:
        return

    app = App()

    # Create two stacks with different environments
    stack1 = SlackIntegrationStack(
        app,
        f"SlackIntegrationStack-{env1}",
        environment=env1,
        runtime_arn=runtime_arn,
        slack_secret_name=f"bedrock-quota-agent/{env1}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    stack2 = SlackIntegrationStack(
        app,
        f"SlackIntegrationStack-{env2}",
        environment=env2,
        runtime_arn=runtime_arn,
        slack_secret_name=f"bedrock-quota-agent/{env2}/slack-credentials",
        dedup_table_name="test-dedup-table",
    )

    # Synthesize both stacks to CloudFormation templates
    template1 = Template.from_stack(stack1)
    template2 = Template.from_stack(stack2)

    template1_json = template1.to_json()
    template2_json = template2.to_json()

    # Get all resources from both templates
    resources1 = template1_json.get('Resources', {})
    resources2 = template2_json.get('Resources', {})

    assert len(resources1) > 0, f"Stack 1 ({env1}) should create at least one resource"
    assert len(resources2) > 0, f"Stack 2 ({env2}) should create at least one resource"

    # Collect physical resource names from both stacks
    # Physical names are what actually get created in AWS (not logical IDs)
    physical_names1 = set()
    physical_names2 = set()

    def extract_physical_names(resources, physical_names_set):
        """Extract physical resource names from CloudFormation resources."""
        for logical_id, resource in resources.items():
            resource_type = resource.get('Type', '')
            properties = resource.get('Properties', {})

            # Extract physical names based on resource type
            if resource_type == 'AWS::Lambda::Function':
                function_name = properties.get('FunctionName')
                if isinstance(function_name, str):
                    physical_names_set.add(('Lambda::Function', function_name))

            elif resource_type == 'AWS::ApiGateway::RestApi':
                api_name = properties.get('Name')
                if isinstance(api_name, str):
                    physical_names_set.add(('ApiGateway::RestApi', api_name))

            elif resource_type == 'AWS::ApiGateway::Stage':
                stage_name = properties.get('StageName')
                if isinstance(stage_name, str):
                    physical_names_set.add(('ApiGateway::Stage', stage_name))

            elif resource_type == 'AWS::Lambda::LayerVersion':
                layer_name = properties.get('LayerName')
                if isinstance(layer_name, str):
                    physical_names_set.add(('Lambda::LayerVersion', layer_name))

            elif resource_type == 'AWS::IAM::Role':
                role_name = properties.get('RoleName')
                if isinstance(role_name, str):
                    physical_names_set.add(('IAM::Role', role_name))

            elif resource_type == 'AWS::Logs::LogGroup':
                log_group_name = properties.get('LogGroupName')
                if isinstance(log_group_name, str):
                    physical_names_set.add(('Logs::LogGroup', log_group_name))

    extract_physical_names(resources1, physical_names1)
    extract_physical_names(resources2, physical_names2)

    # Check for conflicts in physical resource names
    conflicts = physical_names1.intersection(physical_names2)

    assert len(conflicts) == 0, (
        f"Found {len(conflicts)} resource name conflicts between environments "
        f"'{env1}' and '{env2}'. Conflicting resources: {conflicts}. "
        f"Resources should be isolated by environment to prevent deployment conflicts. "
        f"Stack 1 ({env1}) resources: {physical_names1}. "
        f"Stack 2 ({env2}) resources: {physical_names2}."
    )

    # Also verify CloudFormation output export names don't conflict
    outputs1 = template1_json.get('Outputs', {})
    outputs2 = template2_json.get('Outputs', {})

    export_names1 = set()
    export_names2 = set()

    for output_id, output_config in outputs1.items():
        export_name = output_config.get('Export', {}).get('Name')
        if isinstance(export_name, str):
            export_names1.add(export_name)

    for output_id, output_config in outputs2.items():
        export_name = output_config.get('Export', {}).get('Name')
        if isinstance(export_name, str):
            export_names2.add(export_name)

    # Check for conflicts in export names
    export_conflicts = export_names1.intersection(export_names2)

    assert len(export_conflicts) == 0, (
        f"Found {len(export_conflicts)} CloudFormation export name conflicts "
        f"between environments '{env1}' and '{env2}'. "
        f"Conflicting exports: {export_conflicts}. "
        f"Export names must be unique across environments to allow cross-stack references. "
        f"Stack 1 ({env1}) exports: {export_names1}. "
        f"Stack 2 ({env2}) exports: {export_names2}."
    )

    # Verify that at least some resources have physical names
    # (to ensure the test isn't passing vacuously)
    assert len(physical_names1) > 0 or len(physical_names2) > 0, (
        "At least one stack should have resources with physical names. "
        "This test may not be checking anything meaningful."
    )
