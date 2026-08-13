"""Property-based tests for AgentCore stack."""

from hypothesis import given, strategies as st, settings
from unittest.mock import patch
from aws_cdk import App
from aws_cdk.assertions import Template
from infra.stacks.application_stack import ApplicationStack


@settings(deadline=None, max_examples=20)  # Disable deadline for CDK synthesis operations
@given(
    environment=st.text(
        min_size=1,
        max_size=20,
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
    ).filter(lambda x: x and x[0].isalpha())  # Must start with a letter
)
def test_stack_naming_includes_environment_identifier(environment: str):
    """
    Property 1: Stack naming includes environment identifier
    
    For any environment identifier string, when creating a stack with that environment,
    the stack name should contain the environment identifier.
    """
    # Create a CDK app
    app = App()
    
    # Mock the resource creation methods to avoid heavy synthesis
    # We only care about stack naming, not the actual resource creation
    with patch.object(ApplicationStack, '_create_memory_resource'), \
         patch.object(ApplicationStack, '_create_iam_role'), \
         patch.object(ApplicationStack, '_create_ssm_parameters'), \
         patch.object(ApplicationStack, '_create_ecr_repository'), \
         patch.object(ApplicationStack, '_create_agentcore_runtime'), \
         patch.object(ApplicationStack, '_create_outputs'), \
         patch.object(ApplicationStack, '_apply_tags'):
        
        # Create a stack with the environment parameter
        stack_id = f"TestStack-{environment}"
        stack = ApplicationStack(
            app,
            stack_id,
            environment=environment
        )
        
        # Verify that the stack name contains the environment identifier
        assert environment in stack.stack_name, (
            f"Stack name '{stack.stack_name}' does not contain environment identifier '{environment}'"
        )
        
        # Verify that the stack ID also contains the environment
        assert environment in stack_id, (
            f"Stack ID '{stack_id}' does not contain environment identifier '{environment}'"
        )
        
        # Verify that the environment is stored in the stack
        assert hasattr(stack, 'env_name'), "Stack should have env_name attribute"
        assert stack.env_name == environment, (
            f"Stack env_name '{stack.env_name}' does not match environment '{environment}'"
        )


@settings(deadline=None, max_examples=100)
@given(
    environment=st.sampled_from(["dev", "staging", "prod", "test", "demo"])
)
def test_all_required_resource_types_present(environment: str):
    """
    Property 2: All required resource types present in synthesized template
    
    For any synthesized CloudFormation template from the AgentCore stack,
    the template should contain resources of types: IAM::Role, SSM::Parameter,
    and custom resources for Memory and Runtime.
    
    Note: ECR repository is created via CDK DockerImageAsset which uses the asset
    publishing system and doesn't create an AWS::ECR::Repository resource in the template.
    """
    # Create a CDK app
    app = App()
    
    # Create the stack
    stack = ApplicationStack(
        app,
        f"TestStack-{environment}",
        environment=environment
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Verify IAM Role exists
    # The stack creates multiple IAM roles:
    # - 1 Agent IAM role
    # - 2 roles for Memory custom resource (Lambda handler + framework)
    # - 2 roles for Runtime custom resource (Lambda handler + framework)
    template.resource_count_is("AWS::IAM::Role", 5)
    
    # Note: ECR Repository is NOT in the template - it's created by CDK asset system
    # The DockerImageAsset construct handles ECR repository creation outside CloudFormation
    
    # Verify SSM Parameters exist (memory-id, region, role-arn)
    template.resource_count_is("AWS::SSM::Parameter", 3)
    
    # Verify Lambda Functions exist (for custom resource providers)
    # 4 Lambda functions: 2 for Memory (handler + framework), 2 for Runtime (handler + framework)
    template.resource_count_is("AWS::Lambda::Function", 4)
    
    # Verify Custom Resources exist (Memory and Runtime)
    # Custom resources are represented as AWS::CloudFormation::CustomResource
    template.resource_count_is("AWS::CloudFormation::CustomResource", 2)
    
    # Verify CloudFormation Outputs exist
    # The stack should have 5 outputs: RuntimeArn, RepositoryUri, RoleArn, MemoryId, MemoryArn
    outputs = template.to_json().get("Outputs", {})
    assert len(outputs) >= 4, f"Expected at least 4 outputs, found {len(outputs)}"
    
    # Verify specific output keys exist
    output_keys = set(outputs.keys())
    required_outputs = {"RuntimeArn", "RepositoryUri", "RoleArn", "MemoryId"}
    assert required_outputs.issubset(output_keys), (
        f"Missing required outputs. Expected {required_outputs}, found {output_keys}"
    )


@settings(deadline=None, max_examples=100)
@given(
    environment=st.sampled_from(["dev", "staging", "prod", "test", "demo"])
)
def test_memory_id_referenced_by_dependent_resources(environment: str):
    """
    Property 6: Memory ID referenced by dependent resources
    
    For any synthesized template with a memory resource, the memory ID should be
    referenced by at least one SSM parameter and the AgentCore runtime resource.
    """
    # Create a CDK app
    app = App()
    
    # Create the stack
    stack = ApplicationStack(
        app,
        f"TestStack-{environment}",
        environment=environment
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    template_json = template.to_json()
    
    # Find the Memory custom resource
    memory_resource_id = None
    
    resources = template_json.get("Resources", {})
    for resource_id, resource in resources.items():
        if resource.get("Type") == "AWS::CloudFormation::CustomResource":
            properties = resource.get("Properties", {})
            # Check if this is the Memory resource (has MemoryName property)
            if "MemoryName" in properties:
                memory_resource_id = resource_id
                # The memory ID is accessed via GetAtt
                break
    
    assert memory_resource_id is not None, "Memory custom resource not found in template"
    
    # Track references to the memory ID
    ssm_references = []
    runtime_references = []
    
    # Check SSM parameters for memory ID references
    for resource_id, resource in resources.items():
        if resource.get("Type") == "AWS::SSM::Parameter":
            properties = resource.get("Properties", {})
            parameter_name = properties.get("Name", "")
            parameter_value = properties.get("Value", "")
            
            # Check if this is the memory-id parameter
            if "memory-id" in parameter_name:
                # Verify it references the memory resource
                if isinstance(parameter_value, dict):
                    if parameter_value.get("Fn::GetAtt", [None])[0] == memory_resource_id:
                        ssm_references.append(resource_id)
    
    # Check AgentCore Runtime custom resource for memory ID references
    for resource_id, resource in resources.items():
        if resource.get("Type") == "AWS::CloudFormation::CustomResource":
            properties = resource.get("Properties", {})
            # Check if this is the Runtime resource (has ImageUri property)
            if "ImageUri" in properties:
                # Check if MemoryId property references the memory resource
                memory_id_prop = properties.get("MemoryId", "")
                if isinstance(memory_id_prop, dict):
                    if memory_id_prop.get("Fn::GetAtt", [None])[0] == memory_resource_id:
                        runtime_references.append(resource_id)
    
    # Verify that at least one SSM parameter references the memory ID
    assert len(ssm_references) >= 1, (
        f"Expected at least 1 SSM parameter to reference memory ID, found {len(ssm_references)}"
    )
    
    # Verify that the AgentCore runtime references the memory ID
    assert len(runtime_references) >= 1, (
        f"Expected AgentCore runtime to reference memory ID, found {len(runtime_references)}"
    )


@settings(deadline=None, max_examples=100)
@given(
    environment=st.sampled_from(["dev", "staging", "prod", "test", "demo"])
)
def test_all_required_cloudformation_outputs_present(environment: str):
    """
    Property 17: All required CloudFormation outputs present

    For any synthesized stack template, the outputs section should contain entries
    for RuntimeArn, RepositoryUri, RoleArn, and MemoryId.
    """
    # Create a CDK app
    app = App()

    # Create the stack
    stack = ApplicationStack(
        app,
        f"TestStack-{environment}",
        environment=environment
    )

    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    template_json = template.to_json()

    # Get the outputs section
    outputs = template_json.get("Outputs", {})

    # Define the required outputs
    required_outputs = {
        "RuntimeArn": "ARN of the AgentCore runtime",
        "RepositoryUri": "URI of the ECR repository",
        "RoleArn": "ARN of the IAM role",
        "MemoryId": "ID of the memory resource"
    }

    # Verify all required outputs are present
    output_keys = set(outputs.keys())
    required_keys = set(required_outputs.keys())

    assert required_keys.issubset(output_keys), (
        f"Missing required outputs. Expected {required_keys}, found {output_keys}. "
        f"Missing: {required_keys - output_keys}"
    )

    # Verify each output has the required properties
    for output_name, expected_description_keyword in required_outputs.items():
        output = outputs[output_name]

        # Verify output has a Value
        assert "Value" in output, (
            f"Output '{output_name}' is missing 'Value' property"
        )

        # Verify output has a Description
        assert "Description" in output, (
            f"Output '{output_name}' is missing 'Description' property"
        )

        # Verify description contains expected keyword
        description = output["Description"]
        assert expected_description_keyword.lower() in description.lower(), (
            f"Output '{output_name}' description '{description}' does not contain "
            f"expected keyword '{expected_description_keyword}'"
        )

        # Verify output has an Export name
        assert "Export" in output, (
            f"Output '{output_name}' is missing 'Export' property"
        )

        export = output["Export"]
        assert "Name" in export, (
            f"Output '{output_name}' Export is missing 'Name' property"
        )

        # Verify export name includes stack name
        export_name = export["Name"]
        if isinstance(export_name, str):
            assert output_name in export_name, (
                f"Export name '{export_name}' for output '{output_name}' "
                f"does not contain the output name"
            )
        elif isinstance(export_name, dict):
            # Export name might be a CloudFormation intrinsic function
            # In this case, we just verify it's properly structured
            assert "Fn::Sub" in export_name or "Fn::Join" in export_name or "Ref" in export_name, (
                f"Export name for output '{output_name}' uses unexpected intrinsic function: {export_name}"
            )

    # Verify that output values reference actual resources
    # RuntimeArn should reference the runtime custom resource
    runtime_arn_value = outputs["RuntimeArn"]["Value"]
    assert isinstance(runtime_arn_value, dict), (
        "RuntimeArn value should be a CloudFormation reference"
    )
    assert "Fn::GetAtt" in runtime_arn_value, (
        "RuntimeArn should use Fn::GetAtt to reference the runtime resource"
    )

    # RepositoryUri should reference the ECR repository (may use Fn::GetAtt or Fn::Join)
    repository_uri_value = outputs["RepositoryUri"]["Value"]
    assert isinstance(repository_uri_value, dict), (
        "RepositoryUri value should be a CloudFormation reference"
    )
    # Repository URI can be constructed using Fn::Join or Fn::GetAtt
    assert "Fn::GetAtt" in repository_uri_value or "Fn::Join" in repository_uri_value, (
        "RepositoryUri should use CloudFormation intrinsic functions to reference the ECR repository"
    )

    # RoleArn should reference the IAM role
    role_arn_value = outputs["RoleArn"]["Value"]
    assert isinstance(role_arn_value, dict), (
        "RoleArn value should be a CloudFormation reference"
    )
    assert "Fn::GetAtt" in role_arn_value, (
        "RoleArn should use Fn::GetAtt to reference the IAM role"
    )

    # MemoryId should reference the memory custom resource
    memory_id_value = outputs["MemoryId"]["Value"]
    assert isinstance(memory_id_value, dict), (
        "MemoryId value should be a CloudFormation reference"
    )
    assert "Fn::GetAtt" in memory_id_value, (
        "MemoryId should use Fn::GetAtt to reference the memory resource"
    )


@settings(deadline=None, max_examples=100)
@given(
    environment=st.sampled_from(["dev", "staging", "prod", "test", "demo"])
)
def test_all_resources_tagged_with_project_and_environment(environment: str):
    """
    Property 16: All resources tagged with project and environment
    
    For any resource in the synthesized template, the resource should have tags
    including "Project" and "Environment" keys.
    """
    # Create a CDK app
    app = App()
    
    # Create the stack
    stack = ApplicationStack(
        app,
        f"TestStack-{environment}",
        environment=environment
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    template_json = template.to_json()
    
    # Get all resources from the template
    resources = template_json.get("Resources", {})
    
    # Define resource types that should be tagged
    # Note: Some resources like Lambda functions and custom resources may not support tags directly
    # but the stack-level tags should propagate to taggable resources
    taggable_resource_types = {
        "AWS::IAM::Role",
        "AWS::ECR::Repository",
        "AWS::SSM::Parameter",
    }
    
    # Track resources that should have tags
    resources_to_check = []
    for resource_id, resource in resources.items():
        resource_type = resource.get("Type", "")
        if resource_type in taggable_resource_types:
            resources_to_check.append((resource_id, resource_type, resource))
    
    # Verify we have resources to check
    assert len(resources_to_check) > 0, (
        "No taggable resources found in the template"
    )
    
    # Check each taggable resource for required tags
    resources_without_tags = []
    resources_missing_project_tag = []
    resources_missing_environment_tag = []
    
    for resource_id, resource_type, resource in resources_to_check:
        properties = resource.get("Properties", {})
        tags = properties.get("Tags", [])
        
        # Tags can be in different formats depending on the resource type
        # Most resources use an array of {Key, Value} objects
        # Some resources might use a dictionary format
        
        if isinstance(tags, list):
            # Array format: [{"Key": "Project", "Value": "BedrockQuotaAgent"}, ...]
            tag_dict = {tag.get("Key"): tag.get("Value") for tag in tags if isinstance(tag, dict)}
        elif isinstance(tags, dict):
            # Dictionary format: {"Project": "BedrockQuotaAgent", ...}
            tag_dict = tags
        else:
            # No tags found
            tag_dict = {}
        
        # Check for required tags
        if not tag_dict:
            resources_without_tags.append((resource_id, resource_type))
            continue
        
        if "Project" not in tag_dict:
            resources_missing_project_tag.append((resource_id, resource_type))
        
        if "Environment" not in tag_dict:
            resources_missing_environment_tag.append((resource_id, resource_type))
        
        # Verify tag values if tags are present
        if "Project" in tag_dict:
            project_value = tag_dict["Project"]
            assert project_value == "BedrockQuotaAgent", (
                f"Resource '{resource_id}' has incorrect Project tag value: "
                f"expected 'BedrockQuotaAgent', got '{project_value}'"
            )
        
        if "Environment" in tag_dict:
            environment_value = tag_dict["Environment"]
            assert environment_value == environment, (
                f"Resource '{resource_id}' has incorrect Environment tag value: "
                f"expected '{environment}', got '{environment_value}'"
            )
    
    # Report any resources without tags
    if resources_without_tags:
        resource_list = ", ".join([f"{rid} ({rtype})" for rid, rtype in resources_without_tags])
        assert False, (
            f"The following resources have no tags: {resource_list}"
        )
    
    # Report any resources missing Project tag
    if resources_missing_project_tag:
        resource_list = ", ".join([f"{rid} ({rtype})" for rid, rtype in resources_missing_project_tag])
        assert False, (
            f"The following resources are missing 'Project' tag: {resource_list}"
        )
    
    # Report any resources missing Environment tag
    if resources_missing_environment_tag:
        resource_list = ", ".join([f"{rid} ({rtype})" for rid, rtype in resources_missing_environment_tag])
        assert False, (
            f"The following resources are missing 'Environment' tag: {resource_list}"
        )


@settings(deadline=None, max_examples=20)
@given(
    environment=st.one_of(
        # Standard environment names
        st.sampled_from(["dev", "staging", "prod", "test", "demo", "qa", "uat", "beta", "alpha"]),
        # Custom environment names with various patterns
        st.text(
            min_size=1,
            max_size=30,
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
        ).filter(lambda x: x and x[0].isalpha() and not x.endswith("-"))
    )
)
def test_stack_supports_multiple_environment_parameters(environment: str):
    """
    Property 15: Stack supports multiple environment parameters
    
    For any valid environment name (dev, staging, prod, or custom), the stack should
    accept it as a parameter and successfully synthesize without errors.
    """
    # Create a CDK app
    app = App()
    
    # Attempt to create the stack with the environment parameter
    try:
        stack = ApplicationStack(
            app,
            f"TestStack-{environment}",
            environment=environment
        )
        
        # Verify the stack was created successfully
        assert stack is not None, "Stack creation returned None"
        
        # Verify the environment is stored correctly
        assert hasattr(stack, 'env_name'), "Stack should have env_name attribute"
        assert stack.env_name == environment, (
            f"Stack env_name '{stack.env_name}' does not match environment '{environment}'"
        )
        
        # Verify the stack name contains the environment
        assert environment in stack.stack_name, (
            f"Stack name '{stack.stack_name}' does not contain environment '{environment}'"
        )
        
        # Attempt to synthesize the CloudFormation template
        # This is the critical test - the stack should synthesize without errors
        template = Template.from_stack(stack)
        template_json = template.to_json()
        
        # Verify the template is valid
        assert template_json is not None, "Template synthesis returned None"
        assert "Resources" in template_json, "Template missing Resources section"
        assert len(template_json["Resources"]) > 0, "Template has no resources"
        
        # Verify the environment tag is applied to resources
        resources = template_json.get("Resources", {})
        taggable_resource_types = {
            "AWS::IAM::Role",
            "AWS::ECR::Repository",
            "AWS::SSM::Parameter",
        }
        
        # Find at least one taggable resource and verify it has the environment tag
        found_tagged_resource = False
        for resource_id, resource in resources.items():
            resource_type = resource.get("Type", "")
            if resource_type in taggable_resource_types:
                properties = resource.get("Properties", {})
                tags = properties.get("Tags", [])
                
                if isinstance(tags, list):
                    tag_dict = {tag.get("Key"): tag.get("Value") for tag in tags if isinstance(tag, dict)}
                elif isinstance(tags, dict):
                    tag_dict = tags
                else:
                    continue
                
                if "Environment" in tag_dict:
                    assert tag_dict["Environment"] == environment, (
                        f"Resource '{resource_id}' has incorrect Environment tag: "
                        f"expected '{environment}', got '{tag_dict['Environment']}'"
                    )
                    found_tagged_resource = True
                    break
        
        assert found_tagged_resource, (
            "No taggable resources found with Environment tag in the synthesized template"
        )
        
    except ValueError as e:
        # If a ValueError is raised, it should be for a good reason (e.g., invalid characters)
        # Re-raise to fail the test
        raise AssertionError(
            f"Stack creation failed with ValueError for environment '{environment}': {str(e)}"
        )
    except Exception as e:
        # Any other exception during stack creation or synthesis is a failure
        raise AssertionError(
            f"Stack creation or synthesis failed for environment '{environment}': {type(e).__name__}: {str(e)}"
        )

