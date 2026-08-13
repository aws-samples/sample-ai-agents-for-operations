"""Property-based tests for AgentCore runtime construct."""

from hypothesis import given, strategies as st, settings
from aws_cdk import App, Stack
from aws_cdk.assertions import Template
from infra.custom_constructs.application_stack.agentcore_runtime import AgentCoreRuntime


@settings(deadline=None)  # Disable deadline for CDK synthesis operations
@given(
    image_uri=st.one_of(
        # URI with digest format (most common for CDK)
        st.builds(
            lambda repo, hash: f"123456789012.dkr.ecr.us-east-1.amazonaws.com/{repo}@sha256:{hash}",
            repo=st.just("bedrock-quota-agent"),
            hash=st.text(min_size=64, max_size=64, alphabet='0123456789abcdef'),
        ),
        # URI with tag format (content hash)
        st.builds(
            lambda repo, tag: f"123456789012.dkr.ecr.us-east-1.amazonaws.com/{repo}:{tag}",
            repo=st.just("bedrock-quota-agent"),
            tag=st.text(min_size=7, max_size=40, alphabet='0123456789abcdef'),
        ),
    ),
    role_arn=st.builds(
        lambda account, role: f"arn:aws:iam::{account}:role/{role}",
        account=st.text(min_size=12, max_size=12, alphabet='0123456789'),
        role=st.text(
            min_size=1,
            max_size=30,
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="-_"
            )
        ).filter(lambda x: x[0].isalnum() if x else False),
    ),
    memory_id=st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="-_"
        )
    ).filter(lambda x: x[0].isalnum() if x else False),
)
def test_agentcore_runtime_references_correct_image_uri(image_uri: str, role_arn: str, memory_id: str):
    """
    Verify AgentCore runtime references correct image URI.
    
    For any AgentCore runtime resource in the stack, the runtime's image URI property
    should reference the ECR repository created in the same stack.
    
    This test verifies that when an AgentCore runtime is created with a specific image URI,
    the synthesized CloudFormation template contains a custom resource with the ImageUri
    property set to the provided value.
    """
    # Create a test stack with the runtime construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the AgentCore runtime construct
    AgentCoreRuntime(
        stack,
        "TestRuntime",
        image_uri=image_uri,
        role_arn=role_arn,
        memory_id=memory_id
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the template as a dictionary
    template_dict = template.to_json()
    
    # Find custom resources in the template
    custom_resources = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::CloudFormation::CustomResource" or 
           v["Type"].startswith("Custom::")
    }
    
    # Should have at least one custom resource (the runtime resource)
    assert len(custom_resources) > 0, "No custom resource found in template"
    
    # Find the runtime custom resource by checking properties
    runtime_custom_resource = None
    for resource_id, resource in custom_resources.items():
        properties = resource.get("Properties", {})
        # Runtime resource should have ImageUri, RoleArn, and MemoryId properties
        if "ImageUri" in properties and "RoleArn" in properties and "MemoryId" in properties:
            runtime_custom_resource = resource
            break
    
    assert runtime_custom_resource is not None, (
        "Runtime custom resource not found in template"
    )
    
    # Extract the ImageUri property
    actual_image_uri = runtime_custom_resource["Properties"]["ImageUri"]
    
    # Verify that the image URI matches what was provided
    assert actual_image_uri == image_uri, (
        f"Runtime ImageUri should be '{image_uri}', but got '{actual_image_uri}'"
    )
    
    # Verify the image URI format is valid
    # Should contain either a tag (with :) or a digest (with @sha256:)
    assert ':' in actual_image_uri or '@sha256:' in actual_image_uri, (
        f"Image URI '{actual_image_uri}' should contain either a tag (:) or digest (@sha256:)"
    )
    
    # Verify the image URI contains the repository name
    assert "bedrock-quota-agent" in actual_image_uri, (
        f"Image URI '{actual_image_uri}' should contain repository name 'bedrock-quota-agent'"
    )
    
    # Verify the image URI contains ECR registry pattern
    assert ".dkr.ecr." in actual_image_uri and ".amazonaws.com/" in actual_image_uri, (
        f"Image URI '{actual_image_uri}' should be from an ECR registry"
    )



@settings(deadline=None)  # Disable deadline for CDK synthesis operations
@given(
    image_uri=st.one_of(
        # URI with digest format (most common for CDK)
        st.builds(
            lambda repo, hash: f"123456789012.dkr.ecr.us-east-1.amazonaws.com/{repo}@sha256:{hash}",
            repo=st.just("bedrock-quota-agent"),
            hash=st.text(min_size=64, max_size=64, alphabet='0123456789abcdef'),
        ),
        # URI with tag format (content hash)
        st.builds(
            lambda repo, tag: f"123456789012.dkr.ecr.us-east-1.amazonaws.com/{repo}:{tag}",
            repo=st.just("bedrock-quota-agent"),
            tag=st.text(min_size=7, max_size=40, alphabet='0123456789abcdef'),
        ),
    ),
    role_arn=st.builds(
        lambda account, role: f"arn:aws:iam::{account}:role/{role}",
        account=st.text(min_size=12, max_size=12, alphabet='0123456789'),
        role=st.text(
            min_size=1,
            max_size=30,
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="-_"
            )
        ).filter(lambda x: x[0].isalnum() if x else False),
    ),
    memory_id=st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="-_"
        )
    ).filter(lambda x: x[0].isalnum() if x else False),
)
def test_agentcore_runtime_references_iam_role(image_uri: str, role_arn: str, memory_id: str):
    """
    Verify AgentCore runtime references IAM role.
    
    For any AgentCore runtime resource in the stack, the runtime's role ARN property
    should reference the IAM role created in the same stack.
    
    This test verifies that when an AgentCore runtime is created with a specific IAM role ARN,
    the synthesized CloudFormation template contains a custom resource with the RoleArn
    property set to the provided value.
    """
    # Create a test stack with the runtime construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the AgentCore runtime construct
    AgentCoreRuntime(
        stack,
        "TestRuntime",
        image_uri=image_uri,
        role_arn=role_arn,
        memory_id=memory_id
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the template as a dictionary
    template_dict = template.to_json()
    
    # Find custom resources in the template
    custom_resources = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::CloudFormation::CustomResource" or 
           v["Type"].startswith("Custom::")
    }
    
    # Should have at least one custom resource (the runtime resource)
    assert len(custom_resources) > 0, "No custom resource found in template"
    
    # Find the runtime custom resource by checking properties
    runtime_custom_resource = None
    for resource_id, resource in custom_resources.items():
        properties = resource.get("Properties", {})
        # Runtime resource should have ImageUri, RoleArn, and MemoryId properties
        if "ImageUri" in properties and "RoleArn" in properties and "MemoryId" in properties:
            runtime_custom_resource = resource
            break
    
    assert runtime_custom_resource is not None, (
        "Runtime custom resource not found in template"
    )
    
    # Extract the RoleArn property
    actual_role_arn = runtime_custom_resource["Properties"]["RoleArn"]
    
    # Verify that the role ARN matches what was provided
    assert actual_role_arn == role_arn, (
        f"Runtime RoleArn should be '{role_arn}', but got '{actual_role_arn}'"
    )
    
    # Verify the role ARN format is valid
    assert actual_role_arn.startswith("arn:aws:iam::"), (
        f"Role ARN '{actual_role_arn}' should start with 'arn:aws:iam::'"
    )
    
    # Verify the role ARN contains the role path
    assert ":role/" in actual_role_arn, (
        f"Role ARN '{actual_role_arn}' should contain ':role/'"
    )
    
    # Verify the account ID is 12 digits
    parts = actual_role_arn.split(":")
    if len(parts) >= 5:
        account_id = parts[4]
        assert len(account_id) == 12 and account_id.isdigit(), (
            f"Account ID in role ARN should be 12 digits, got '{account_id}'"
        )
    
    # Verify the role name is not empty
    role_name = actual_role_arn.split("/")[-1]
    assert len(role_name) > 0, (
        "Role name in ARN should not be empty"
    )


@settings(deadline=None)  # Disable deadline for CDK synthesis operations
@given(
    image_uri=st.one_of(
        # URI with digest format (most common for CDK)
        st.builds(
            lambda repo, hash: f"123456789012.dkr.ecr.us-east-1.amazonaws.com/{repo}@sha256:{hash}",
            repo=st.just("bedrock-quota-agent"),
            hash=st.text(min_size=64, max_size=64, alphabet='0123456789abcdef'),
        ),
        # URI with tag format (content hash)
        st.builds(
            lambda repo, tag: f"123456789012.dkr.ecr.us-east-1.amazonaws.com/{repo}:{tag}",
            repo=st.just("bedrock-quota-agent"),
            tag=st.text(min_size=7, max_size=40, alphabet='0123456789abcdef'),
        ),
    ),
    role_arn=st.builds(
        lambda account, role: f"arn:aws:iam::{account}:role/{role}",
        account=st.text(min_size=12, max_size=12, alphabet='0123456789'),
        role=st.text(
            min_size=1,
            max_size=30,
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="-_"
            )
        ).filter(lambda x: x[0].isalnum() if x else False),
    ),
    memory_id=st.text(
        min_size=1,
        max_size=50,
        alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_'
    ).filter(lambda x: x and x[0].isalnum()),
)
def test_agentcore_runtime_references_memory_resource(image_uri: str, role_arn: str, memory_id: str):
    """
    Verify AgentCore runtime references memory resource.
    
    For any AgentCore runtime resource in the stack, the runtime's memory ID property
    should reference the memory resource created in the same stack.
    
    This test verifies that when an AgentCore runtime is created with a specific memory ID,
    the synthesized CloudFormation template contains a custom resource with the MemoryId
    property set to the provided value.
    """
    # Create a test stack with the runtime construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the AgentCore runtime construct
    AgentCoreRuntime(
        stack,
        "TestRuntime",
        image_uri=image_uri,
        role_arn=role_arn,
        memory_id=memory_id
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the template as a dictionary
    template_dict = template.to_json()
    
    # Find custom resources in the template
    custom_resources = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::CloudFormation::CustomResource" or 
           v["Type"].startswith("Custom::")
    }
    
    # Should have at least one custom resource (the runtime resource)
    assert len(custom_resources) > 0, "No custom resource found in template"
    
    # Find the runtime custom resource by checking properties
    runtime_custom_resource = None
    for resource_id, resource in custom_resources.items():
        properties = resource.get("Properties", {})
        # Runtime resource should have ImageUri, RoleArn, and MemoryId properties
        if "ImageUri" in properties and "RoleArn" in properties and "MemoryId" in properties:
            runtime_custom_resource = resource
            break
    
    assert runtime_custom_resource is not None, (
        "Runtime custom resource not found in template"
    )
    
    # Extract the MemoryId property
    actual_memory_id = runtime_custom_resource["Properties"]["MemoryId"]
    
    # Verify that the memory ID matches what was provided
    assert actual_memory_id == memory_id, (
        f"Runtime MemoryId should be '{memory_id}', but got '{actual_memory_id}'"
    )
    
    # Verify the memory ID format is valid (alphanumeric with hyphens/underscores)
    assert len(actual_memory_id) > 0, (
        "Memory ID should not be empty"
    )
    
    # Verify the memory ID starts with an alphanumeric character
    assert actual_memory_id[0].isalnum(), (
        f"Memory ID '{actual_memory_id}' should start with an alphanumeric character"
    )
    
    # Verify the memory ID contains only valid characters
    valid_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_')
    assert all(c in valid_chars for c in actual_memory_id), (
        f"Memory ID '{actual_memory_id}' should only contain alphanumeric characters, hyphens, and underscores"
    )
