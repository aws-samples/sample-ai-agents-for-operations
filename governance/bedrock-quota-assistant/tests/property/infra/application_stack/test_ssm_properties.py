"""Property-based tests for SSM parameters construct."""

from hypothesis import given, strategies as st, settings
from aws_cdk import App, Stack
from aws_cdk.assertions import Template
from infra.custom_constructs.application_stack.ssm_parameters import AgentSsmParameters


@settings(deadline=None)  # Disable deadline for CDK synthesis operations
@given(
    parameter_namespace=st.sampled_from([
        "/bedrock-quota-agent/",
        "/custom-namespace/",
        "/test/",
        "/my-app/config/",
        "/prod/agent/",
    ]),
    parameters=st.dictionaries(
        keys=st.text(
            min_size=1,
            max_size=30,
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        ).filter(lambda x: x and x[0].isalnum()),
        values=st.text(min_size=1, max_size=100),
        min_size=1,
        max_size=5
    )
)
def test_all_ssm_parameters_use_correct_namespace_prefix(
    parameter_namespace: str,
    parameters: dict
):
    """
    Verify all SSM parameters use correct namespace prefix.
    
    For any SSM parameter created by the stack, the parameter name should start with
    the namespace prefix `/bedrock-quota-agent/`.
    """
    # Create a test stack with the SSM parameters construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the SSM parameters construct
    AgentSsmParameters(
        stack,
        "TestSsmParameters",
        parameters=parameters,
        parameter_namespace=parameter_namespace
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the template as a dictionary
    template_dict = template.to_json()
    
    # Find all SSM parameters in the template
    ssm_parameters = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::SSM::Parameter"
    }
    
    # Should have at least one SSM parameter
    assert len(ssm_parameters) > 0, "No SSM parameters found in template"
    
    # Verify that we have the expected number of parameters
    assert len(ssm_parameters) == len(parameters), (
        f"Expected {len(parameters)} SSM parameters, but found {len(ssm_parameters)}"
    )
    
    # Normalize the namespace (ensure it ends with /)
    expected_namespace = parameter_namespace.rstrip("/") + "/"
    
    # Verify each SSM parameter uses the correct namespace prefix
    for resource_id, resource in ssm_parameters.items():
        properties = resource.get("Properties", {})
        parameter_name = properties.get("Name")
        
        assert parameter_name is not None, (
            f"SSM parameter {resource_id} does not have a Name property"
        )
        
        # Verify the parameter name starts with the expected namespace
        assert parameter_name.startswith(expected_namespace), (
            f"SSM parameter '{parameter_name}' does not start with namespace prefix '{expected_namespace}'"
        )
        
        # Verify the parameter name format: namespace + parameter key
        # Extract the parameter key (everything after the namespace)
        param_key = parameter_name[len(expected_namespace):]
        
        # The parameter key should be one of the keys from the input parameters dict
        assert param_key in parameters, (
            f"Parameter key '{param_key}' from parameter name '{parameter_name}' "
            f"is not in the input parameters: {list(parameters.keys())}"
        )


@settings(deadline=None)  # Disable deadline for CDK synthesis operations
@given(
    parameters=st.dictionaries(
        keys=st.text(
            min_size=1,
            max_size=30,
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        ).filter(lambda x: x and x[0].isalnum()),
        values=st.text(min_size=1, max_size=100),
        min_size=1,
        max_size=5
    )
)
def test_all_ssm_parameters_use_string_type(parameters: dict):
    """
    Verify all SSM parameters use String type.
    
    For any SSM parameter created by the stack, the parameter type should be "String".
    """
    # Create a test stack with the SSM parameters construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the SSM parameters construct
    AgentSsmParameters(
        stack,
        "TestSsmParameters",
        parameters=parameters,
        parameter_namespace="/bedrock-quota-agent/"
    )
    
    # Synthesize the CloudFormation template
    template = Template.from_stack(stack)
    
    # Get the template as a dictionary
    template_dict = template.to_json()
    
    # Find all SSM parameters in the template
    ssm_parameters = {
        k: v for k, v in template_dict["Resources"].items()
        if v["Type"] == "AWS::SSM::Parameter"
    }
    
    # Should have at least one SSM parameter
    assert len(ssm_parameters) > 0, "No SSM parameters found in template"
    
    # Verify that we have the expected number of parameters
    assert len(ssm_parameters) == len(parameters), (
        f"Expected {len(parameters)} SSM parameters, but found {len(ssm_parameters)}"
    )
    
    # Verify each SSM parameter uses String type
    for resource_id, resource in ssm_parameters.items():
        properties = resource.get("Properties", {})
        parameter_type = properties.get("Type")
        parameter_name = properties.get("Name", resource_id)
        
        # Verify the parameter has a Type property
        assert parameter_type is not None, (
            f"SSM parameter '{parameter_name}' does not have a Type property"
        )
        
        # Verify the parameter type is "String"
        assert parameter_type == "String", (
            f"SSM parameter '{parameter_name}' has type '{parameter_type}', expected 'String'"
        )
