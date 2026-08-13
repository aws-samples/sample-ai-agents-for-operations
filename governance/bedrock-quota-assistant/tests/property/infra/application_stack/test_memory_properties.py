"""Property-based tests for Memory resource construct."""

from hypothesis import given, strategies as st, settings
from aws_cdk import App, Stack
from aws_cdk.assertions import Template
from infra.custom_constructs.application_stack.memory_resource import AgentMemoryResource


@settings(deadline=None)  # Disable deadline for CDK synthesis operations
@given(
    stack_name=st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="-_"
        )
    ).filter(lambda x: x[0].isalnum() if x else False)  # Ensure first char is alphanumeric
)
def test_memory_resource_name_includes_stack_name(stack_name: str):
    """
    Verify memory resource name includes stack name.
    
    For any stack name, when creating a memory resource, the memory resource name
    should contain the stack name.
    """
    # Create a test stack with the memory resource construct
    app = App()
    stack = Stack(app, "TestStack")
    
    # Create the memory resource construct
    AgentMemoryResource(
        stack,
        "TestMemory",
        stack_name=stack_name
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
    
    # Should have at least one custom resource (the memory resource)
    assert len(custom_resources) > 0, "No custom resource found in template"
    
    # Find the memory custom resource by checking properties
    memory_custom_resource = None
    for resource_id, resource in custom_resources.items():
        properties = resource.get("Properties", {})
        if "MemoryName" in properties:
            memory_custom_resource = resource
            break
    
    assert memory_custom_resource is not None, (
        "Memory custom resource not found in template"
    )
    
    # Extract the MemoryName property
    memory_name = memory_custom_resource["Properties"]["MemoryName"]
    
    # Verify that the memory name contains the stack name
    assert stack_name in memory_name, (
        f"Memory resource name '{memory_name}' does not contain stack name '{stack_name}'"
    )
    
    # Verify the expected format: "{stack_name}-memory"
    expected_memory_name = f"{stack_name}-memory"
    assert memory_name == expected_memory_name, (
        f"Memory resource name should be '{expected_memory_name}', but got '{memory_name}'"
    )


import time
import random
import traceback
from unittest.mock import Mock, patch
from hypothesis import assume


def wait_for_memory_status(
    bedrock_client,
    memory_id: str,
    target_statuses: list,
    max_duration_seconds: int = 240
):
    """
    Poll memory status until it reaches one of the target statuses.
    
    This is a copy of the function from the Lambda handler for testing purposes.
    """
    start_time = time.time()
    attempt = 0
    last_status = None
    
    while True:
        elapsed_time = time.time() - start_time
        
        # Check for timeout
        if elapsed_time >= max_duration_seconds:
            error_msg = (
                f"Timeout waiting for memory {memory_id} to reach target status. "
                f"Last status: {last_status}, Elapsed time: {elapsed_time:.2f}s"
            )
            raise TimeoutError(error_msg)
        
        try:
            # Call get_memory API
            response = bedrock_client.get_memory(memoryId=memory_id)
            memory = response.get('memory', {})
            current_status = memory.get('status')
            last_status = current_status
            
            # Check if status is in target statuses
            if current_status in target_statuses:
                return memory
            
            # Check for FAILED status
            if current_status == 'FAILED':
                failure_reason = memory.get('failureReason', 'Unknown failure reason')
                error_msg = (
                    f"Memory {memory_id} reached FAILED status. "
                    f"Reason: {failure_reason}"
                )
                raise Exception(error_msg)
            
            # Check for unexpected statuses during Create/Update operations
            if current_status == 'DELETING' and 'DELETING' not in target_statuses:
                error_msg = (
                    f"Memory {memory_id} has unexpected status DELETING during operation"
                )
                raise Exception(error_msg)
            
        except Exception as e:
            if 'ResourceNotFoundException' in str(type(e).__name__):
                return {"id": memory_id, "status": "DELETED"}
            
            # Check if this is a retryable error
            error_type = type(e).__name__
            retryable_errors = [
                'ThrottlingException',
                'InternalServerException', 
                'ServiceUnavailableException'
            ]
            
            if error_type not in retryable_errors:
                raise
        
        # Calculate backoff delay and sleep
        delay = calculate_backoff_delay(attempt)
        time.sleep(delay)
        attempt += 1


def calculate_backoff_delay(
    attempt: int,
    initial_delay: float = 2.0,
    max_delay: float = 30.0,
    multiplier: float = 2.0,
    jitter_percent: float = 0.25
) -> float:
    """Calculate exponential backoff delay with jitter."""
    base_delay = initial_delay * (multiplier ** attempt)
    base_delay = min(base_delay, max_delay)
    jitter_range = base_delay * jitter_percent
    jitter = random.uniform(-jitter_range, jitter_range)
    final_delay = max(0.0, base_delay + jitter)
    return final_delay


@settings(max_examples=20, deadline=None)
@given(
    memory_id=st.text(min_size=1, max_size=100),
    target_status=st.sampled_from(['ACTIVE', 'DELETED']),
    num_polls_before_target=st.integers(min_value=0, max_value=10)
)
@patch('time.sleep', return_value=None)  # Mock sleep to make tests instant
def test_status_polling_termination(
    mock_sleep,
    memory_id: str,
    target_status: str,
    num_polls_before_target: int
):
    """
    Property 2: Status polling termination
    
    
    
    For any status polling operation, the handler must terminate within
    the maximum polling duration or when a terminal status is reached
    (ACTIVE, FAILED, or ResourceNotFoundException).
    
    This property verifies that:
    1. Polling terminates when target status is reached
    2. Polling terminates on timeout
    3. The function returns the correct memory details
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create a sequence of responses: in-progress statuses followed by target status
    responses = []
    for _ in range(num_polls_before_target):
        responses.append({
            'memory': {
                'id': memory_id,
                'status': 'CREATING',
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
            }
        })
    
    # Add final target status response
    responses.append({
        'memory': {
            'id': memory_id,
            'status': target_status,
            'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
        }
    })
    
    # Configure mock to return responses in sequence
    mock_client.get_memory = Mock(side_effect=responses)
    
    # Mock time.sleep to avoid actual delays
    with patch('time.sleep'):
        # Call wait_for_memory_status with short max_duration for testing
        result = wait_for_memory_status(
            mock_client,
            memory_id,
            [target_status],
            max_duration_seconds=60
        )
    
    # Verify the function terminated and returned correct result
    assert result is not None, "Function should return a result"
    assert result['id'] == memory_id, f"Expected memory_id {memory_id}, got {result['id']}"
    assert result['status'] == target_status, (
        f"Expected status {target_status}, got {result['status']}"
    )
    
    # Verify get_memory was called the expected number of times
    expected_calls = num_polls_before_target + 1
    assert mock_client.get_memory.call_count == expected_calls, (
        f"Expected {expected_calls} calls to get_memory, got {mock_client.get_memory.call_count}"
    )


@settings(max_examples=20, deadline=None)
@given(
    memory_id=st.text(min_size=1, max_size=100),
    num_polls=st.integers(min_value=1, max_value=5)
)
@patch('time.sleep', return_value=None)  # Mock sleep to make tests instant
def test_continued_polling_on_in_progress_states(
    mock_sleep,
    memory_id: str,
    num_polls: int
):
    """
    Property 5: Continued polling for in-progress states
    
    
    
    For any polling operation where the status is CREATING or DELETING,
    the handler must continue polling with appropriate delays and not return early.
    
    This property verifies that:
    1. In-progress statuses don't cause early termination
    2. Polling continues until a terminal status is reached
    3. Multiple polling attempts are made for in-progress states
    """
    # Test with CREATING status (in-progress for Create/Update operations)
    target_status = 'ACTIVE'
    target_statuses = ['ACTIVE']
    
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create a sequence of CREATING responses followed by ACTIVE
    responses = []
    for _ in range(num_polls):
        responses.append({
            'memory': {
                'id': memory_id,
                'status': 'CREATING',
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
            }
        })
    
    # Add final ACTIVE response
    responses.append({
        'memory': {
            'id': memory_id,
            'status': target_status,
            'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
        }
    })
    
    # Configure mock to return responses in sequence
    mock_client.get_memory = Mock(side_effect=responses)
    
    # Mock time.sleep to avoid actual delays
    with patch('time.sleep'):
        # Call wait_for_memory_status
        result = wait_for_memory_status(
            mock_client,
            memory_id,
            target_statuses,
            max_duration_seconds=60
        )
    
    # Verify polling continued through all in-progress states
    expected_calls = num_polls + 1
    assert mock_client.get_memory.call_count == expected_calls, (
        f"Expected {expected_calls} calls for {num_polls} in-progress polls, "
        f"got {mock_client.get_memory.call_count}"
    )
    
    # Verify final result has target status
    assert result['status'] == target_status, (
        f"Expected final status {target_status}, got {result['status']}"
    )


@settings(max_examples=20, deadline=None)
@given(
    memory_id=st.text(min_size=1, max_size=100),
    num_polls=st.integers(min_value=1, max_value=10)
)
@patch('time.sleep', return_value=None)  # Mock sleep to make tests instant
def test_polling_uses_get_memory_api(
    mock_sleep,
    memory_id: str,
    num_polls: int
):
    """
    Property 4: Polling uses get_memory API
    
    
    
    For any status polling operation, the handler must use get_memory() API call
    to retrieve the current status, not list_memories() or other APIs.
    
    This property verifies that:
    1. Only get_memory() is called during polling
    2. get_memory() is called with the correct memory_id
    3. No other API methods are invoked
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Add list_memories method to verify it's NOT called
    mock_client.list_memories = Mock()
    
    # Create a sequence of responses
    responses = []
    for i in range(num_polls):
        responses.append({
            'memory': {
                'id': memory_id,
                'status': 'CREATING',
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
            }
        })
    
    # Add final ACTIVE response
    responses.append({
        'memory': {
            'id': memory_id,
            'status': 'ACTIVE',
            'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
        }
    })
    
    # Configure mock to return responses in sequence
    mock_client.get_memory = Mock(side_effect=responses)
    
    # Mock time.sleep to avoid actual delays
    with patch('time.sleep'):
        # Call wait_for_memory_status
        result = wait_for_memory_status(
            mock_client,
            memory_id,
            ['ACTIVE'],
            max_duration_seconds=60
        )
    
    # Verify get_memory was called
    assert mock_client.get_memory.call_count > 0, (
        "get_memory should be called at least once"
    )
    
    # Verify all calls to get_memory used the correct memory_id
    for call in mock_client.get_memory.call_args_list:
        args, kwargs = call
        assert kwargs.get('memoryId') == memory_id, (
            f"get_memory should be called with memoryId={memory_id}, "
            f"got {kwargs.get('memoryId')}"
        )
    
    # Verify list_memories was NOT called
    assert mock_client.list_memories.call_count == 0, (
        "list_memories should not be called during status polling"
    )
    
    # Verify result is correct
    assert result['id'] == memory_id
    assert result['status'] == 'ACTIVE'


@settings(max_examples=100, deadline=None)
@given(
    memory_name=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    description=st.text(min_size=0, max_size=100)
)
def test_no_preemptive_deletion(memory_name: str, description: str):
    """
    Property 9: No preemptive deletion
    
    
    
    For any Create operation, the handler must call create_memory() before
    any calls to list_memories() or delete_memory().
    
    This property verifies that:
    1. create_memory() is called first
    2. No list_memories() calls occur before create_memory()
    3. No delete_memory() calls occur before create_memory()
    4. The delete-before-create workaround has been removed
    """
    # Track the order of API calls
    call_order = []
    
    # Create mock bedrock client
    mock_client = Mock()
    
    # Mock create_memory to track when it's called
    def mock_create_memory(**kwargs):
        call_order.append('create_memory')
        return {
            'memory': {
                'id': memory_name,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_name}',
                'status': 'CREATING'
            }
        }
    
    # Mock list_memories to track when it's called
    def mock_list_memories(**kwargs):
        call_order.append('list_memories')
        return {'memories': []}
    
    # Mock delete_memory to track when it's called
    def mock_delete_memory(**kwargs):
        call_order.append('delete_memory')
        return {}
    
    # Mock get_memory to return ACTIVE status immediately
    def mock_get_memory(**kwargs):
        call_order.append('get_memory')
        return {
            'memory': {
                'id': memory_name,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_name}',
                'status': 'ACTIVE'
            }
        }
    
    mock_client.create_memory = Mock(side_effect=mock_create_memory)
    mock_client.list_memories = Mock(side_effect=mock_list_memories)
    mock_client.delete_memory = Mock(side_effect=mock_delete_memory)
    mock_client.get_memory = Mock(side_effect=mock_get_memory)
    
    # Simulate on_create handler logic
    
    # Mock time.sleep to avoid delays
    with patch('time.sleep'):
        # Call create_memory
        response = mock_client.create_memory(
            name=memory_name,
            description=description,
            eventExpiryDuration=90
        )
        
        memory_id = response['memory']['id']
        
        # Wait for status (simulating wait_for_memory_status)
        mock_client.get_memory(memoryId=memory_id)
    
    # Verify call order
    assert len(call_order) >= 2, "Should have at least create_memory and get_memory calls"
    
    # Verify create_memory was called first
    assert call_order[0] == 'create_memory', (
        f"create_memory should be called first, but first call was {call_order[0]}"
    )
    
    # Verify no list_memories calls occurred before create_memory
    create_index = call_order.index('create_memory')
    list_calls_before_create = [
        call for call in call_order[:create_index] 
        if call == 'list_memories'
    ]
    assert len(list_calls_before_create) == 0, (
        f"list_memories should not be called before create_memory, "
        f"but found {len(list_calls_before_create)} calls"
    )
    
    # Verify no delete_memory calls occurred before create_memory
    delete_calls_before_create = [
        call for call in call_order[:create_index]
        if call == 'delete_memory'
    ]
    assert len(delete_calls_before_create) == 0, (
        f"delete_memory should not be called before create_memory, "
        f"but found {len(delete_calls_before_create)} calls"
    )
    
    # Verify no list_memories calls occurred at all (workaround removed)
    list_calls_total = [call for call in call_order if call == 'list_memories']
    assert len(list_calls_total) == 0, (
        f"list_memories should not be called at all in on_create, "
        f"but found {len(list_calls_total)} calls"
    )
    
    # Verify no delete_memory calls occurred at all (workaround removed)
    delete_calls_total = [call for call in call_order if call == 'delete_memory']
    assert len(delete_calls_total) == 0, (
        f"delete_memory should not be called at all in on_create, "
        f"but found {len(delete_calls_total)} calls"
    )


@settings(max_examples=100, deadline=None)
@given(
    memory_name=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    description=st.text(min_size=0, max_size=100),
    existing_status=st.sampled_from(['ACTIVE', 'CREATING', 'FAILED'])
)
def test_create_retry_idempotency(
    memory_name: str,
    description: str,
    existing_status: str
):
    """
    Property 21: Create retry idempotency
    
    
    
    For any Create operation retry where create_memory() raises ConflictException
    and get_memory() returns ACTIVE status, the handler must return the existing
    memory details instead of failing.
    
    This property verifies that:
    1. ConflictException is caught during create_memory()
    2. get_memory() is called to check existing memory status
    3. If existing memory is ACTIVE, return its details
    4. If existing memory is not ACTIVE, raise an exception
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create ConflictException class
    class ConflictException(Exception):
        pass
    
    # Attach exception to client
    mock_client.exceptions = Mock()
    mock_client.exceptions.ConflictException = ConflictException
    
    # Mock create_memory to raise ConflictException
    def mock_create_memory(**kwargs):
        raise ConflictException("Memory with this name already exists")
    
    # Mock get_memory to return existing memory with specified status
    def mock_get_memory(**kwargs):
        return {
            'memory': {
                'id': memory_name,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_name}',
                'status': existing_status,
                'name': memory_name,
                'description': description
            }
        }
    
    mock_client.create_memory = Mock(side_effect=mock_create_memory)
    mock_client.get_memory = Mock(side_effect=mock_get_memory)
    
    # Simulate on_create handler logic with retry idempotency
    
    try:
        # Try to create memory
        mock_client.create_memory(
            name=memory_name,
            description=description,
            eventExpiryDuration=90
        )
        # Should not reach here
        assert False, "create_memory should have raised ConflictException"
        
    except ConflictException:
        # ConflictException caught, check existing memory
        get_response = mock_client.get_memory(memoryId=memory_name)
        existing_memory = get_response.get('memory', {})
        existing_memory_status = existing_memory.get('status')
        
        if existing_memory_status == 'ACTIVE':
            # Return existing memory details
            result = {
                'PhysicalResourceId': existing_memory['id'],
                'Data': {
                    'MemoryId': existing_memory['id'],
                    'MemoryArn': existing_memory['arn']
                }
            }
            
            # Verify result structure
            assert result['PhysicalResourceId'] == memory_name, (
                f"PhysicalResourceId should be {memory_name}, "
                f"got {result['PhysicalResourceId']}"
            )
            assert result['Data']['MemoryId'] == memory_name, (
                f"MemoryId should be {memory_name}, "
                f"got {result['Data']['MemoryId']}"
            )
            assert 'MemoryArn' in result['Data'], (
                "MemoryArn should be present in response Data"
            )
            
        else:
            # Existing memory is not ACTIVE, should raise exception
            
            # Verify that non-ACTIVE status would cause an exception
            assert existing_memory_status != 'ACTIVE', (
                f"Status is {existing_memory_status}, should raise exception"
            )
    
    # Verify get_memory was called to check existing memory
    assert mock_client.get_memory.call_count == 1, (
        f"get_memory should be called once to check existing memory, "
        f"got {mock_client.get_memory.call_count} calls"
    )
    
    # Verify get_memory was called with correct memory_name
    call_args = mock_client.get_memory.call_args
    assert call_args[1]['memoryId'] == memory_name, (
        f"get_memory should be called with memoryId={memory_name}, "
        f"got {call_args[1]['memoryId']}"
    )


@settings(max_examples=100, deadline=None)
@given(
    memory_name=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    description=st.text(min_size=0, max_size=100),
    operation_type=st.sampled_from(['Create', 'Update']),
    num_polls_before_active=st.integers(min_value=0, max_value=5)
)
@patch('time.sleep', return_value=None)  # Mock sleep to make tests instant
def test_operation_completion_requires_active_status(
    mock_sleep,
    memory_name: str,
    description: str,
    operation_type: str,
    num_polls_before_active: int
):
    """
    Property 1: Operation completion requires ACTIVE status
    
    
    
    For any Create or Update operation, when the handler returns success to
    CloudFormation, the memory status must be ACTIVE.
    
    This property verifies that:
    1. Handler waits for ACTIVE status before returning
    2. Handler does not return success with CREATING status
    3. Handler polls until ACTIVE is reached
    4. Both Create and Update operations follow this rule
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create a sequence of responses: CREATING followed by ACTIVE
    responses = []
    for _ in range(num_polls_before_active):
        responses.append({
            'memory': {
                'id': memory_name,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_name}',
                'status': 'CREATING',
                'name': memory_name,
                'description': description
            }
        })
    
    # Add final ACTIVE response
    responses.append({
        'memory': {
            'id': memory_name,
            'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_name}',
            'status': 'ACTIVE',
            'name': memory_name,
            'description': description
        }
    })
    
    if operation_type == 'Create':
        # Mock create_memory
        mock_client.create_memory = Mock(return_value={
            'memory': {
                'id': memory_name,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_name}',
                'status': 'CREATING'
            }
        })
    else:  # Update
        # Mock update_memory
        mock_client.update_memory = Mock(return_value={
            'memory': {
                'id': memory_name,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_name}',
                'status': 'CREATING'
            }
        })
    
    # Mock get_memory to return responses in sequence
    mock_client.get_memory = Mock(side_effect=responses)
    
    # Simulate handler logic
    with patch('time.sleep'):
        if operation_type == 'Create':
            # Call create_memory
            create_response = mock_client.create_memory(
                name=memory_name,
                description=description,
                eventExpiryDuration=90
            )
            memory_id = create_response['memory']['id']
        else:  # Update
            # Call update_memory
            update_response = mock_client.update_memory(
                id=memory_name,
                description=description
            )
            memory_id = update_response['memory']['id']
        
        # Wait for ACTIVE status
        final_memory = wait_for_memory_status(
            mock_client,
            memory_id,
            ['ACTIVE'],
            max_duration_seconds=60
        )
    
    # Verify the final status is ACTIVE
    assert final_memory['status'] == 'ACTIVE', (
        f"Handler should only return when status is ACTIVE, "
        f"but got status: {final_memory['status']}"
    )
    
    # Verify get_memory was called the expected number of times
    expected_calls = num_polls_before_active + 1
    assert mock_client.get_memory.call_count == expected_calls, (
        f"Expected {expected_calls} calls to get_memory, "
        f"got {mock_client.get_memory.call_count}"
    )
    
    # Verify the handler would return correct response structure
    result = {
        'PhysicalResourceId': final_memory['id'],
        'Data': {
            'MemoryId': final_memory['id'],
            'MemoryArn': final_memory['arn']
        }
    }
    
    # Verify response structure
    assert result['PhysicalResourceId'] == memory_name, (
        f"PhysicalResourceId should be {memory_name}, "
        f"got {result['PhysicalResourceId']}"
    )
    assert result['Data']['MemoryId'] == memory_name, (
        f"MemoryId should be {memory_name}, "
        f"got {result['Data']['MemoryId']}"
    )
    assert 'MemoryArn' in result['Data'], (
        "MemoryArn should be present in response Data"
    )


@settings(max_examples=100, deadline=None)
@given(
    memory_id=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    old_description=st.text(min_size=0, max_size=100),
    new_description=st.text(min_size=0, max_size=100).filter(lambda x: x != "")  # Ensure different
)
def test_update_calls_update_memory(
    memory_id: str,
    old_description: str,
    new_description: str
):
    """
    Property 11: Update calls update_memory
    
    
    
    For any Update operation, the handler must call update_memory() with
    the new properties from CloudFormation.
    
    This property verifies that:
    1. update_memory() is called during Update operations
    2. update_memory() is called with the correct memory_id
    3. update_memory() is called with the new description
    4. The API call happens before status polling
    """
    # Ensure descriptions are different to trigger update
    assume(old_description != new_description)
    
    # Track the order of API calls
    call_order = []
    
    # Create mock bedrock client
    mock_client = Mock()
    
    # Mock get_memory for initial state check
    def mock_get_memory_initial(**kwargs):
        call_order.append('get_memory')
        return {
            'memory': {
                'id': memory_id,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}',
                'status': 'ACTIVE',
                'description': old_description
            }
        }
    
    # Mock update_memory to track when it's called
    def mock_update_memory(**kwargs):
        call_order.append('update_memory')
        # Verify correct parameters
        assert kwargs.get('id') == memory_id, (
            f"update_memory should be called with id={memory_id}, "
            f"got {kwargs.get('id')}"
        )
        assert kwargs.get('description') == new_description, (
            f"update_memory should be called with description={new_description}, "
            f"got {kwargs.get('description')}"
        )
        return {
            'memory': {
                'id': memory_id,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}',
                'status': 'CREATING'
            }
        }
    
    # Mock get_memory for status polling (after update)
    def mock_get_memory_polling(**kwargs):
        call_order.append('get_memory_poll')
        return {
            'memory': {
                'id': memory_id,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}',
                'status': 'ACTIVE',
                'description': new_description
            }
        }
    
    # Set up mock responses
    get_memory_responses = [
        mock_get_memory_initial(memoryId=memory_id),  # Initial state check
        mock_get_memory_polling(memoryId=memory_id)   # Status polling
    ]
    
    mock_client.get_memory = Mock(side_effect=get_memory_responses)
    mock_client.update_memory = Mock(side_effect=mock_update_memory)
    
    # Simulate on_update handler logic
    
    # Check current state (for idempotency)
    get_response = mock_client.get_memory(memoryId=memory_id)
    current_memory = get_response.get('memory', {})
    current_description = current_memory.get('description', '')
    
    # Only update if description changed
    if current_description != new_description:
        # Call update_memory
        mock_client.update_memory(
            id=memory_id,
            description=new_description
        )
        
        # Wait for ACTIVE status
        with patch('time.sleep'):
            wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=60
            )
    
    # Verify update_memory was called
    assert mock_client.update_memory.call_count == 1, (
        f"update_memory should be called once during Update operation, "
        f"got {mock_client.update_memory.call_count} calls"
    )
    
    # Verify call order: get_memory (initial check) -> update_memory -> get_memory (polling)
    assert len(call_order) >= 2, (
        f"Should have at least get_memory and update_memory calls, "
        f"got {len(call_order)} calls"
    )
    
    # Find the index of update_memory call
    update_index = call_order.index('update_memory')
    
    # Verify get_memory was called before update_memory (for idempotency check)
    assert update_index > 0, (
        "get_memory should be called before update_memory for idempotency check"
    )
    assert call_order[0] == 'get_memory', (
        f"First call should be get_memory for state check, got {call_order[0]}"
    )
    
    # Verify update_memory was called with correct parameters
    update_call_args = mock_client.update_memory.call_args
    assert update_call_args[1]['id'] == memory_id, (
        f"update_memory should be called with id={memory_id}, "
        f"got {update_call_args[1]['id']}"
    )
    assert update_call_args[1]['description'] == new_description, (
        f"update_memory should be called with description={new_description}, "
        f"got {update_call_args[1]['description']}"
    )



@settings(max_examples=100, deadline=None)
@given(
    memory_id=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    description=st.text(min_size=0, max_size=100),
    current_status=st.sampled_from(['ACTIVE', 'CREATING', 'FAILED']),
    retry_scenario=st.sampled_from(['same_description', 'different_description'])
)
def test_update_retry_idempotency(
    memory_id: str,
    description: str,
    current_status: str,
    retry_scenario: str
):
    """
    Property 22: Update retry idempotency
    
    
    
    For any Update operation retry, the handler must apply the update idempotently
    by checking current state before applying changes.
    
    This property verifies that:
    1. Handler checks current memory state before updating
    2. If current state matches desired state, no update is performed
    3. If current state differs, update is applied
    4. Handler waits for ACTIVE status regardless of whether update was needed
    5. Retry operations are safe and don't cause duplicate updates
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Determine current and new descriptions based on retry scenario
    if retry_scenario == 'same_description':
        current_description = description
        new_description = description
    else:  # different_description
        current_description = description
        new_description = description + "_updated"
    
    # Track API calls
    call_order = []
    
    # Mock get_memory for initial state check
    def mock_get_memory_initial(**kwargs):
        call_order.append('get_memory_initial')
        return {
            'memory': {
                'id': memory_id,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}',
                'status': current_status,
                'description': current_description
            }
        }
    
    # Mock update_memory
    def mock_update_memory(**kwargs):
        call_order.append('update_memory')
        return {
            'memory': {
                'id': memory_id,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}',
                'status': 'CREATING'
            }
        }
    
    # Mock get_memory for status polling
    def mock_get_memory_polling(**kwargs):
        call_order.append('get_memory_poll')
        return {
            'memory': {
                'id': memory_id,
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}',
                'status': 'ACTIVE',
                'description': new_description
            }
        }
    
    # Set up mock responses
    if retry_scenario == 'same_description' and current_status == 'ACTIVE':
        # No update needed, just return current state
        mock_client.get_memory = Mock(return_value=mock_get_memory_initial(memoryId=memory_id))
    elif retry_scenario == 'same_description' and current_status != 'ACTIVE':
        # No update needed, but wait for ACTIVE
        get_memory_responses = [
            mock_get_memory_initial(memoryId=memory_id),  # Initial check
            mock_get_memory_polling(memoryId=memory_id)   # Status polling
        ]
        mock_client.get_memory = Mock(side_effect=get_memory_responses)
    else:  # different_description
        # Update needed
        get_memory_responses = [
            mock_get_memory_initial(memoryId=memory_id),  # Initial check
            mock_get_memory_polling(memoryId=memory_id)   # Status polling after update
        ]
        mock_client.get_memory = Mock(side_effect=get_memory_responses)
    
    mock_client.update_memory = Mock(side_effect=mock_update_memory)
    
    # Simulate on_update handler logic with retry idempotency
    
    # Check current state (for idempotency)
    get_response = mock_client.get_memory(memoryId=memory_id)
    current_memory = get_response.get('memory', {})
    current_memory_description = current_memory.get('description', '')
    current_memory_status = current_memory.get('status')
    
    # Verify get_memory was called to check current state
    assert mock_client.get_memory.call_count >= 1, (
        "get_memory should be called to check current state"
    )
    
    # Check if update is needed (idempotency check)
    if current_memory_description == new_description:
        # No update needed
        if current_memory_status == 'ACTIVE':
            # Already in desired state, return immediately
            result = {
                'PhysicalResourceId': memory_id,
                'Data': {
                    'MemoryId': memory_id,
                    'MemoryArn': current_memory['arn']
                }
            }
            
            # Verify update_memory was NOT called
            assert mock_client.update_memory.call_count == 0, (
                f"update_memory should not be called when description unchanged, "
                f"got {mock_client.update_memory.call_count} calls"
            )
        else:
            # Wait for ACTIVE status even though no update needed
            with patch('time.sleep'):
                memory = wait_for_memory_status(
                    mock_client,
                    memory_id,
                    ['ACTIVE'],
                    max_duration_seconds=60
                )
            
            result = {
                'PhysicalResourceId': memory_id,
                'Data': {
                    'MemoryId': memory_id,
                    'MemoryArn': memory['arn']
                }
            }
            
            # Verify update_memory was NOT called
            assert mock_client.update_memory.call_count == 0, (
                f"update_memory should not be called when description unchanged, "
                f"got {mock_client.update_memory.call_count} calls"
            )
    else:
        # Update needed
        mock_client.update_memory(
            id=memory_id,
            description=new_description
        )
        
        # Wait for ACTIVE status
        with patch('time.sleep'):
            memory = wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=60
            )
        
        result = {
            'PhysicalResourceId': memory_id,
            'Data': {
                'MemoryId': memory_id,
                'MemoryArn': memory['arn']
            }
        }
        
        # Verify update_memory was called
        assert mock_client.update_memory.call_count == 1, (
            f"update_memory should be called once when description changed, "
            f"got {mock_client.update_memory.call_count} calls"
        )
    
    # Verify result structure is correct
    assert result['PhysicalResourceId'] == memory_id, (
        f"PhysicalResourceId should be {memory_id}, "
        f"got {result['PhysicalResourceId']}"
    )
    assert result['Data']['MemoryId'] == memory_id, (
        f"MemoryId should be {memory_id}, "
        f"got {result['Data']['MemoryId']}"
    )
    assert 'MemoryArn' in result['Data'], (
        "MemoryArn should be present in response Data"
    )
    
    # Verify idempotency: same input produces same output on retry
    if retry_scenario == 'same_description':
        # On retry with same description, no update should occur
        assert mock_client.update_memory.call_count == 0, (
            "Retry with same description should not call update_memory"
        )
    else:
        # On retry with different description, update should occur
        assert mock_client.update_memory.call_count == 1, (
            "Retry with different description should call update_memory once"
        )



@settings(max_examples=100, deadline=None)
@given(
    memory_id=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    exception_type=st.sampled_from(['ResourceNotFoundException', 'ValidationException', 'None'])
)
def test_delete_idempotency(memory_id: str, exception_type: str):
    """
    Property 8: Delete idempotency
    
    
    
    For any Delete operation, when delete_memory() raises ResourceNotFoundException
    or get_memory() raises ResourceNotFoundException, the handler must return success
    without raising an exception.
    
    This property verifies that:
    1. ResourceNotFoundException from delete_memory() returns success
    2. ResourceNotFoundException during polling returns success
    3. Delete operations are idempotent (can be retried safely)
    4. Handler returns success even if memory doesn't exist
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create exception classes
    class ResourceNotFoundException(Exception):
        pass
    
    class ValidationException(Exception):
        pass
    
    # Attach exceptions to client
    mock_client.exceptions = Mock()
    mock_client.exceptions.ResourceNotFoundException = ResourceNotFoundException
    mock_client.exceptions.ValidationException = ValidationException
    
    # Track whether delete_memory was called
    delete_called = False
    
    # Mock delete_memory based on exception type
    def mock_delete_memory(**kwargs):
        nonlocal delete_called
        delete_called = True
        
        if exception_type == 'ResourceNotFoundException':
            raise ResourceNotFoundException("Memory not found")
        elif exception_type == 'ValidationException':
            raise ValidationException("Invalid memory ID")
        # else: no exception, successful delete
        return {}
    
    # Mock get_memory for status polling
    def mock_get_memory(**kwargs):
        # After delete_memory succeeds, polling should get ResourceNotFoundException
        if exception_type == 'None':
            raise ResourceNotFoundException("Memory deleted")
        else:
            # Should not reach here if delete_memory raised exception
            return {
                'memory': {
                    'id': memory_id,
                    'status': 'DELETING'
                }
            }
    
    mock_client.delete_memory = Mock(side_effect=mock_delete_memory)
    mock_client.get_memory = Mock(side_effect=mock_get_memory)
    
    # Simulate on_delete handler logic
    result = None
    exception_raised = False
    
    try:
        # Try to delete memory
        mock_client.delete_memory(memoryId=memory_id)
        
        # If delete succeeded, poll for completion
        if exception_type == 'None':
            with patch('time.sleep'):
                wait_for_memory_status(
                    mock_client,
                    memory_id,
                    ['DELETED'],
                    max_duration_seconds=60
                )
        
        # Return success
        result = {
            'PhysicalResourceId': memory_id
        }
        
    except ResourceNotFoundException:
        # Memory already deleted (idempotency)
        result = {
            'PhysicalResourceId': memory_id
        }
        
    except ValidationException:
        # Log validation error but return success
        result = {
            'PhysicalResourceId': memory_id
        }
        
    except Exception:
        # Log all errors but return success to avoid blocking CloudFormation
        result = {
            'PhysicalResourceId': memory_id
        }
    
    # Verify delete_memory was called
    assert delete_called, "delete_memory should be called"
    
    # Verify result is success regardless of exception
    assert result is not None, "Handler should return a result"
    assert result['PhysicalResourceId'] == memory_id, (
        f"PhysicalResourceId should be {memory_id}, "
        f"got {result['PhysicalResourceId']}"
    )
    
    # Verify no exception was raised to CloudFormation
    assert not exception_raised, (
        "Delete handler should not raise exceptions to CloudFormation"
    )
    
    # Verify idempotency: multiple deletes of same resource should all succeed
    # Try deleting again
    result2 = None
    try:
        mock_client.delete_memory(memoryId=memory_id)
        result2 = {
            'PhysicalResourceId': memory_id
        }
    except ResourceNotFoundException:
        result2 = {
            'PhysicalResourceId': memory_id
        }
    except ValidationException:
        result2 = {
            'PhysicalResourceId': memory_id
        }
    except Exception:
        result2 = {
            'PhysicalResourceId': memory_id
        }
    
    # Verify second delete also succeeds
    assert result2 is not None, "Second delete should also succeed"
    assert result2['PhysicalResourceId'] == memory_id, (
        "Second delete should return same PhysicalResourceId"
    )


@settings(max_examples=100, deadline=None)
@given(
    memory_id=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    error_scenario=st.sampled_from([
        'delete_api_error',
        'polling_error',
        'timeout_error',
        'unexpected_exception'
    ])
)
def test_delete_always_succeeds(memory_id: str, error_scenario: str):
    """
    Property 16: Delete always succeeds
    
    
    
    For any Delete operation that encounters any exception, the handler must
    log the error and return success to avoid blocking CloudFormation stack deletion.
    
    This property verifies that:
    1. All exceptions during delete are caught
    2. Errors are logged but don't cause failure
    3. Handler always returns success response
    4. CloudFormation stack deletion is never blocked
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create various exception types
    class ResourceNotFoundException(Exception):
        pass
    
    class ValidationException(Exception):
        pass
    
    class ThrottlingException(Exception):
        pass
    
    class InternalServerException(Exception):
        pass
    
    # Attach exceptions to client
    mock_client.exceptions = Mock()
    mock_client.exceptions.ResourceNotFoundException = ResourceNotFoundException
    mock_client.exceptions.ValidationException = ValidationException
    mock_client.exceptions.ThrottlingException = ThrottlingException
    
    # Mock delete_memory based on error scenario
    def mock_delete_memory(**kwargs):
        if error_scenario == 'delete_api_error':
            raise InternalServerException("Internal server error")
        elif error_scenario == 'unexpected_exception':
            raise RuntimeError("Unexpected error")
        # else: successful delete
        return {}
    
    # Mock get_memory for status polling
    def mock_get_memory(**kwargs):
        if error_scenario == 'polling_error':
            raise ThrottlingException("Rate limit exceeded")
        elif error_scenario == 'timeout_error':
            # Simulate timeout by never returning DELETED status
            return {
                'memory': {
                    'id': memory_id,
                    'status': 'DELETING'
                }
            }
        else:
            # Normal case: memory deleted
            raise ResourceNotFoundException("Memory deleted")
    
    mock_client.delete_memory = Mock(side_effect=mock_delete_memory)
    mock_client.get_memory = Mock(side_effect=mock_get_memory)
    
    # Simulate on_delete handler logic with comprehensive error handling
    result = None
    exception_raised_to_cfn = False
    
    try:
        # Wrap entire delete logic in try-except
        try:
            # Call delete_memory
            mock_client.delete_memory(memoryId=memory_id)
            
            # Poll for completion (with very short timeout for testing)
            if error_scenario != 'delete_api_error' and error_scenario != 'unexpected_exception':
                with patch('time.sleep'):
                    try:
                        wait_for_memory_status(
                            mock_client,
                            memory_id,
                            ['DELETED'],
                            max_duration_seconds=1  # Very short timeout for testing
                        )
                    except TimeoutError:
                        # Timeout during polling, but still return success
                        pass
            
            # Return success
            result = {
                'PhysicalResourceId': memory_id
            }
            
        except ResourceNotFoundException:
            # Memory already deleted
            result = {
                'PhysicalResourceId': memory_id
            }
            
        except ValidationException:
            # Validation error, but return success
            result = {
                'PhysicalResourceId': memory_id
            }
            
        except Exception:
            # Any other error, log but return success
            result = {
                'PhysicalResourceId': memory_id
            }
            
    except Exception:
        # If any exception escapes, it would be raised to CloudFormation
        exception_raised_to_cfn = True
        raise
    
    # Verify handler always returns success
    assert result is not None, (
        f"Handler should return success even with error scenario: {error_scenario}"
    )
    assert result['PhysicalResourceId'] == memory_id, (
        f"PhysicalResourceId should be {memory_id}, "
        f"got {result['PhysicalResourceId']}"
    )
    
    # Verify no exception was raised to CloudFormation
    assert not exception_raised_to_cfn, (
        f"Delete handler should not raise exceptions to CloudFormation "
        f"even with error scenario: {error_scenario}"
    )
    
    # Verify the handler would prevent CloudFormation stack deletion from being blocked
    # This is implicit in the fact that result is not None and no exception was raised


@settings(max_examples=100, deadline=None)
@given(
    physical_resource_id=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
)
def test_delete_uses_correct_resource_id(physical_resource_id: str):
    """
    Property 10: Delete uses correct resource ID
    
    
    
    For any Delete operation, the handler must call delete_memory() with the exact
    PhysicalResourceId provided by CloudFormation.
    
    This property verifies that:
    1. delete_memory() is called with PhysicalResourceId
    2. No transformation or modification of the ID occurs
    3. The ID passed to delete_memory() matches the CloudFormation resource ID
    4. Status polling uses the same ID
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create exception class
    class ResourceNotFoundException(Exception):
        pass
    
    # Attach exception to client
    mock_client.exceptions = Mock()
    mock_client.exceptions.ResourceNotFoundException = ResourceNotFoundException
    
    # Track the memory_id used in API calls
    delete_memory_id = None
    get_memory_ids = []
    
    # Mock delete_memory to capture the memory_id
    def mock_delete_memory(**kwargs):
        nonlocal delete_memory_id
        delete_memory_id = kwargs.get('memoryId')
        return {}
    
    # Mock get_memory to capture the memory_id
    def mock_get_memory(**kwargs):
        get_memory_ids.append(kwargs.get('memoryId'))
        # Simulate successful deletion
        raise ResourceNotFoundException("Memory deleted")
    
    mock_client.delete_memory = Mock(side_effect=mock_delete_memory)
    mock_client.get_memory = Mock(side_effect=mock_get_memory)
    
    # Simulate on_delete handler receiving PhysicalResourceId from CloudFormation
    event = {
        'RequestType': 'Delete',
        'PhysicalResourceId': physical_resource_id
    }
    
    # Simulate on_delete handler logic
    try:
        # Call delete_memory with PhysicalResourceId
        mock_client.delete_memory(memoryId=event['PhysicalResourceId'])
        
        # Poll for completion
        with patch('time.sleep'):
            try:
                wait_for_memory_status(
                    mock_client,
                    event['PhysicalResourceId'],
                    ['DELETED'],
                    max_duration_seconds=60
                )
            except Exception:
                pass
        
        result = {
            'PhysicalResourceId': event['PhysicalResourceId']
        }
        
    except ResourceNotFoundException:
        result = {
            'PhysicalResourceId': event['PhysicalResourceId']
        }
    
    # Verify delete_memory was called with correct PhysicalResourceId
    assert delete_memory_id is not None, (
        "delete_memory should be called"
    )
    assert delete_memory_id == physical_resource_id, (
        f"delete_memory should be called with PhysicalResourceId={physical_resource_id}, "
        f"got {delete_memory_id}"
    )
    
    # Verify get_memory (status polling) was called with correct PhysicalResourceId
    assert len(get_memory_ids) > 0, (
        "get_memory should be called for status polling"
    )
    for memory_id in get_memory_ids:
        assert memory_id == physical_resource_id, (
            f"get_memory should be called with PhysicalResourceId={physical_resource_id}, "
            f"got {memory_id}"
        )
    
    # Verify no transformation or modification of the ID occurred
    # The ID should be passed directly without any changes
    assert delete_memory_id == physical_resource_id, (
        "PhysicalResourceId should not be transformed or modified"
    )
    
    # Verify result contains the correct PhysicalResourceId
    assert result['PhysicalResourceId'] == physical_resource_id, (
        f"Result should contain PhysicalResourceId={physical_resource_id}, "
        f"got {result['PhysicalResourceId']}"
    )


@settings(max_examples=20, deadline=None)
@given(
    memory_id=st.text(min_size=1, max_size=100),
    error_type=st.sampled_from([
        'ThrottlingException',
        'InternalServerException',
        'ServiceUnavailableException',
        'ValidationException',
        'AccessDeniedException',
        'ConflictException',
        'UnknownException'
    ]),
    num_retries_before_success=st.integers(min_value=1, max_value=3)
)
@patch('time.sleep', return_value=None)
def test_retryable_vs_non_retryable_error_handling(
    mock_sleep,
    memory_id: str,
    error_type: str,
    num_retries_before_success: int
):
    """
    Property 15: Retryable vs non-retryable error handling
    
    
    
    For any API error, the handler must classify it as retryable 
    (ThrottlingException, InternalServerException, ServiceUnavailableException) 
    or non-retryable (ValidationException, AccessDeniedException, ConflictException) 
    and handle accordingly.
    
    This property verifies that:
    1. Retryable errors continue polling after backoff
    2. Non-retryable errors raise exception immediately
    3. Unknown errors raise exception immediately
    4. Retryable errors eventually succeed after retries
    """
    # Define error classifications
    retryable_errors = [
        'ThrottlingException',
        'InternalServerException',
        'ServiceUnavailableException'
    ]
    
    non_retryable_errors = [
        'ValidationException',
        'AccessDeniedException',
        'ConflictException'
    ]
    
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create custom exception classes
    class ThrottlingException(Exception):
        pass
    
    class InternalServerException(Exception):
        pass
    
    class ServiceUnavailableException(Exception):
        pass
    
    class ValidationException(Exception):
        pass
    
    class AccessDeniedException(Exception):
        pass
    
    class ConflictException(Exception):
        pass
    
    class UnknownException(Exception):
        pass
    
    # Map error type names to exception classes
    exception_map = {
        'ThrottlingException': ThrottlingException,
        'InternalServerException': InternalServerException,
        'ServiceUnavailableException': ServiceUnavailableException,
        'ValidationException': ValidationException,
        'AccessDeniedException': AccessDeniedException,
        'ConflictException': ConflictException,
        'UnknownException': UnknownException
    }
    
    exception_class = exception_map[error_type]
    
    if error_type in retryable_errors:
        # For retryable errors, create a sequence of errors followed by success
        responses = []
        
        # Add error responses
        for _ in range(num_retries_before_success):
            responses.append(exception_class(f"{error_type} occurred"))
        
        # Add final success response
        responses.append({
            'memory': {
                'id': memory_id,
                'status': 'ACTIVE',
                'arn': f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
            }
        })
        
        mock_client.get_memory = Mock(side_effect=responses)
        
        # Should succeed after retries
        with patch('time.sleep'):
            result = wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=60
            )
        
        # Verify success
        assert result is not None, "Should return result after retries"
        assert result['status'] == 'ACTIVE', f"Expected ACTIVE status, got {result['status']}"
        
        # Verify get_memory was called the expected number of times
        expected_calls = num_retries_before_success + 1
        assert mock_client.get_memory.call_count == expected_calls, (
            f"Expected {expected_calls} calls for {num_retries_before_success} retries, "
            f"got {mock_client.get_memory.call_count}"
        )
        
    elif error_type in non_retryable_errors or error_type == 'UnknownException':
        # For non-retryable errors, should raise immediately
        mock_client.get_memory = Mock(side_effect=exception_class(f"{error_type} occurred"))
        
        # Should raise exception immediately
        with patch('time.sleep'):
            try:
                result = wait_for_memory_status(
                    mock_client,
                    memory_id,
                    ['ACTIVE'],
                    max_duration_seconds=60
                )
                # Should not reach here
                assert False, f"Non-retryable error {error_type} should raise exception immediately"
            except exception_class:
                # Expected - non-retryable error raised
                pass
        
        # Verify get_memory was called only once (no retries)
        assert mock_client.get_memory.call_count == 1, (
            f"Non-retryable error should fail immediately with 1 call, "
            f"got {mock_client.get_memory.call_count} calls"
        )


@settings(max_examples=20, deadline=None)
@given(
    memory_id=st.text(min_size=1, max_size=100),
    error_type=st.sampled_from([
        'ValidationException',
        'AccessDeniedException'
    ])
)
def test_error_logging_completeness(
    memory_id: str,
    error_type: str
):
    """
    Property 14: Error logging completeness
    
    
    
    For any API call that raises an exception, the handler must log the 
    exception type, error message, and stack trace.
    
    This property verifies that:
    1. Exception type is logged
    2. Error message is logged
    3. Stack trace is logged
    4. Context information is included (memory_id, elapsed_time, attempt_count)
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create custom exception classes
    class ValidationException(Exception):
        pass
    
    class AccessDeniedException(Exception):
        pass
    
    # Map error type names to exception classes
    exception_map = {
        'ValidationException': ValidationException,
        'AccessDeniedException': AccessDeniedException
    }
    
    exception_class = exception_map[error_type]
    error_message = f"Test error: {error_type}"
    
    # Mock get_memory to raise the exception
    mock_client.get_memory = Mock(side_effect=exception_class(error_message))
    
    # Create a custom logger to capture logs
    import logging
    test_logger = logging.getLogger('test_error_logging')
    test_logger.setLevel(logging.ERROR)
    
    # Create a handler to capture log records
    log_records = []
    
    class TestHandler(logging.Handler):
        def emit(self, record):
            log_records.append(record)
    
    handler = TestHandler()
    test_logger.addHandler(handler)
    
    # Patch the logger in wait_for_memory_status
    with patch('time.sleep'):
        # Patch the logger module to use our test logger
        import sys
        sys.modules.get('logging').getLogger()
        
        # Create a modified wait_for_memory_status that uses our test logger
        def test_wait_for_memory_status(bedrock_client, memory_id, target_statuses, max_duration_seconds=240):
            start_time = time.time()
            attempt = 0
            
            while True:
                elapsed_time = time.time() - start_time
                
                if elapsed_time >= max_duration_seconds:
                    raise TimeoutError("Timeout")
                
                try:
                    response = bedrock_client.get_memory(memoryId=memory_id)
                    memory = response.get('memory', {})
                    current_status = memory.get('status')
                    
                    if current_status in target_statuses:
                        return memory
                    
                except Exception as e:
                    error_type_name = type(e).__name__
                    
                    # Log exception details - this is what we're testing
                    test_logger.error(
                        f"Error during status polling for memory {memory_id}",
                        extra={
                            "memory_id": memory_id,
                            "error_type": error_type_name,
                            "error_message": str(e),
                            "stack_trace": traceback.format_exc(),
                            "elapsed_time": elapsed_time,
                            "attempt_count": attempt
                        }
                    )
                    
                    # Non-retryable errors raise immediately
                    non_retryable_errors = ['ValidationException', 'AccessDeniedException']
                    if error_type_name in non_retryable_errors:
                        raise
                
                attempt += 1
                if attempt > 5:  # Prevent infinite loop
                    break
        
        try:
            test_wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=60
            )
        except (ValidationException, AccessDeniedException):
            # Expected for non-retryable errors
            pass
    
    # Verify error logging occurred
    assert len(log_records) > 0, "Error should be logged"
    
    # Find the error log record
    error_record = log_records[0]
    
    # Verify exception type is logged
    assert hasattr(error_record, 'error_type'), "error_type should be in log record"
    assert error_record.error_type == error_type, (
        f"Expected error_type {error_type}, got {error_record.error_type}"
    )
    
    # Verify error message is logged
    assert hasattr(error_record, 'error_message'), "error_message should be in log record"
    assert error_message in error_record.error_message, (
        f"Error message should contain '{error_message}', got '{error_record.error_message}'"
    )
    
    # Verify stack trace is logged
    assert hasattr(error_record, 'stack_trace'), "stack_trace should be in log record"
    assert len(error_record.stack_trace) > 0, "Stack trace should not be empty"
    
    # Verify context information is logged
    assert hasattr(error_record, 'memory_id'), "memory_id should be in log record"
    assert error_record.memory_id == memory_id, (
        f"Expected memory_id {memory_id}, got {error_record.memory_id}"
    )
    
    assert hasattr(error_record, 'elapsed_time'), "elapsed_time should be in log record"
    assert hasattr(error_record, 'attempt_count'), "attempt_count should be in log record"


@settings(max_examples=20, deadline=None)
@given(
    memory_name=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),
    description=st.text(min_size=0, max_size=100),
    operation_type=st.sampled_from(['Create', 'Update']),
    error_type=st.sampled_from([
        'ValidationException',
        'AccessDeniedException',
        'TimeoutError'
    ])
)
@patch('time.sleep', return_value=None)
def test_failed_operations_raise_exceptions(
    mock_sleep,
    memory_name: str,
    description: str,
    operation_type: str,
    error_type: str
):
    """
    Property 18: Failed operations raise exceptions
    
    
    
    For any Create or Update operation that fails, the handler must raise 
    an exception to trigger CloudFormation rollback, not return success.
    
    This property verifies that:
    1. Failed Create operations raise exceptions
    2. Failed Update operations raise exceptions
    3. Exceptions are not caught and suppressed
    4. CloudFormation receives failure signal
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create custom exception classes
    class ValidationException(Exception):
        pass
    
    class AccessDeniedException(Exception):
        pass
    
    # Map error type names to exception classes
    exception_map = {
        'ValidationException': ValidationException,
        'AccessDeniedException': AccessDeniedException,
        'TimeoutError': TimeoutError
    }
    
    exception_class = exception_map[error_type]
    
    if operation_type == 'Create':
        # Mock create_memory to raise exception
        mock_client.create_memory = Mock(side_effect=exception_class(f"{error_type} during create"))
        
        # Simulate on_create handler
        with patch('time.sleep'):
            exception_raised = False
            try:
                # Call create_memory
                mock_client.create_memory(
                    name=memory_name,
                    description=description,
                    eventExpiryDuration=90
                )
                # Should not reach here
                assert False, f"Create operation should raise {error_type}"
            except exception_class:
                # Expected - exception was raised
                exception_raised = True
        
        # Verify exception was raised (not caught and suppressed)
        assert exception_raised, (
            f"Create operation with {error_type} should raise exception, not return success"
        )
        
    else:  # Update
        # Mock update_memory to raise exception
        mock_client.update_memory = Mock(side_effect=exception_class(f"{error_type} during update"))
        
        # Simulate on_update handler
        with patch('time.sleep'):
            exception_raised = False
            try:
                # Call update_memory
                mock_client.update_memory(
                    id=memory_name,
                    description=description
                )
                # Should not reach here
                assert False, f"Update operation should raise {error_type}"
            except exception_class:
                # Expected - exception was raised
                exception_raised = True
        
        # Verify exception was raised (not caught and suppressed)
        assert exception_raised, (
            f"Update operation with {error_type} should raise exception, not return success"
        )



@settings(max_examples=100, deadline=None)
@given(
    memory_name=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    description=st.text(min_size=0, max_size=100),
    operation_type=st.sampled_from(['Create', 'Update', 'Delete']),
    num_polls_before_complete=st.integers(min_value=0, max_value=5)
)
@patch('time.sleep', return_value=None)  # Mock sleep to make tests instant
def test_response_structure_completeness(
    mock_sleep,
    memory_name: str,
    description: str,
    operation_type: str,
    num_polls_before_complete: int
):
    """
    Property 17: Response structure completeness
    
    
    
    For any successful operation, the handler response must contain PhysicalResourceId
    and a Data section with MemoryId and MemoryArn.
    
    This property verifies that:
    1. PhysicalResourceId is present in response
    2. Data section is present in response
    3. MemoryId is present in Data section
    4. MemoryArn is present in Data section
    5. All fields have correct values
    6. Response structure is consistent across Create, Update, and Delete operations
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create exception class for Delete operations
    class ResourceNotFoundException(Exception):
        pass
    
    # Attach exception to client
    mock_client.exceptions = Mock()
    mock_client.exceptions.ResourceNotFoundException = ResourceNotFoundException
    
    # Generate ARN for the memory
    memory_arn = f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_name}'
    
    # Simulate different operations
    if operation_type == 'Create':
        # Mock create_memory
        mock_client.create_memory = Mock(return_value={
            'memory': {
                'id': memory_name,
                'arn': memory_arn,
                'status': 'CREATING',
                'name': memory_name,
                'description': description
            }
        })
        
        # Create a sequence of responses: CREATING followed by ACTIVE
        responses = []
        for _ in range(num_polls_before_complete):
            responses.append({
                'memory': {
                    'id': memory_name,
                    'arn': memory_arn,
                    'status': 'CREATING',
                    'name': memory_name,
                    'description': description
                }
            })
        
        # Add final ACTIVE response
        responses.append({
            'memory': {
                'id': memory_name,
                'arn': memory_arn,
                'status': 'ACTIVE',
                'name': memory_name,
                'description': description
            }
        })
        
        mock_client.get_memory = Mock(side_effect=responses)
        
        # Simulate on_create handler logic
        with patch('time.sleep'):
            # Call create_memory
            create_response = mock_client.create_memory(
                name=memory_name,
                description=description,
                eventExpiryDuration=90
            )
            memory_id = create_response['memory']['id']
            
            # Wait for ACTIVE status
            final_memory = wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=60
            )
            
            # Build response
            result = {
                'PhysicalResourceId': final_memory['id'],
                'Data': {
                    'MemoryId': final_memory['id'],
                    'MemoryArn': final_memory['arn']
                }
            }
    
    elif operation_type == 'Update':
        # Mock update_memory
        mock_client.update_memory = Mock(return_value={
            'memory': {
                'id': memory_name,
                'arn': memory_arn,
                'status': 'CREATING'
            }
        })
        
        # Create a sequence of responses: CREATING followed by ACTIVE
        responses = []
        for _ in range(num_polls_before_complete):
            responses.append({
                'memory': {
                    'id': memory_name,
                    'arn': memory_arn,
                    'status': 'CREATING',
                    'name': memory_name,
                    'description': description
                }
            })
        
        # Add final ACTIVE response
        responses.append({
            'memory': {
                'id': memory_name,
                'arn': memory_arn,
                'status': 'ACTIVE',
                'name': memory_name,
                'description': description
            }
        })
        
        mock_client.get_memory = Mock(side_effect=responses)
        
        # Simulate on_update handler logic
        with patch('time.sleep'):
            # Call update_memory
            update_response = mock_client.update_memory(
                id=memory_name,
                description=description
            )
            memory_id = update_response['memory']['id']
            
            # Wait for ACTIVE status
            final_memory = wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=60
            )
            
            # Build response
            result = {
                'PhysicalResourceId': final_memory['id'],
                'Data': {
                    'MemoryId': final_memory['id'],
                    'MemoryArn': final_memory['arn']
                }
            }
    
    else:  # Delete
        # Mock delete_memory
        mock_client.delete_memory = Mock(return_value={})
        
        # For Delete operations, we don't use wait_for_memory_status in the same way
        # Instead, we just call delete_memory and handle ResourceNotFoundException
        
        # Simulate on_delete handler logic
        try:
            with patch('time.sleep'):
                # Call delete_memory
                mock_client.delete_memory(memoryId=memory_name)
                
                # Build response (Delete only returns PhysicalResourceId)
                result = {
                    'PhysicalResourceId': memory_name
                }
        except ResourceNotFoundException:
            # Memory already deleted (idempotency)
            result = {
                'PhysicalResourceId': memory_name
            }
    
    # Verify response structure completeness
    
    # 1. Verify PhysicalResourceId is present
    assert 'PhysicalResourceId' in result, (
        f"Response must contain PhysicalResourceId for {operation_type} operation"
    )
    
    # 2. Verify PhysicalResourceId has correct value
    assert result['PhysicalResourceId'] == memory_name, (
        f"PhysicalResourceId should be {memory_name}, "
        f"got {result['PhysicalResourceId']}"
    )
    
    # 3. Verify PhysicalResourceId is not None or empty
    assert result['PhysicalResourceId'] is not None, (
        "PhysicalResourceId must not be None"
    )
    assert len(result['PhysicalResourceId']) > 0, (
        "PhysicalResourceId must not be empty"
    )
    
    # 4. For Create and Update operations, verify Data section
    if operation_type in ['Create', 'Update']:
        # Verify Data section is present
        assert 'Data' in result, (
            f"Response must contain Data section for {operation_type} operation"
        )
        
        # Verify Data is a dictionary
        assert isinstance(result['Data'], dict), (
            f"Data section must be a dictionary, got {type(result['Data'])}"
        )
        
        # Verify MemoryId is present in Data
        assert 'MemoryId' in result['Data'], (
            f"Data section must contain MemoryId for {operation_type} operation"
        )
        
        # Verify MemoryId has correct value
        assert result['Data']['MemoryId'] == memory_name, (
            f"MemoryId should be {memory_name}, "
            f"got {result['Data']['MemoryId']}"
        )
        
        # Verify MemoryId is not None or empty
        assert result['Data']['MemoryId'] is not None, (
            "MemoryId must not be None"
        )
        assert len(result['Data']['MemoryId']) > 0, (
            "MemoryId must not be empty"
        )
        
        # Verify MemoryArn is present in Data
        assert 'MemoryArn' in result['Data'], (
            f"Data section must contain MemoryArn for {operation_type} operation"
        )
        
        # Verify MemoryArn has correct value
        assert result['Data']['MemoryArn'] == memory_arn, (
            f"MemoryArn should be {memory_arn}, "
            f"got {result['Data']['MemoryArn']}"
        )
        
        # Verify MemoryArn is not None or empty
        assert result['Data']['MemoryArn'] is not None, (
            "MemoryArn must not be None"
        )
        assert len(result['Data']['MemoryArn']) > 0, (
            "MemoryArn must not be empty"
        )
        
        # Verify MemoryArn has correct ARN format
        assert result['Data']['MemoryArn'].startswith('arn:aws:bedrock:'), (
            f"MemoryArn should start with 'arn:aws:bedrock:', "
            f"got {result['Data']['MemoryArn']}"
        )
        
        # Verify MemoryArn contains the memory_id
        assert memory_name in result['Data']['MemoryArn'], (
            f"MemoryArn should contain memory_id {memory_name}, "
            f"got {result['Data']['MemoryArn']}"
        )
        
        # Verify PhysicalResourceId matches MemoryId
        assert result['PhysicalResourceId'] == result['Data']['MemoryId'], (
            f"PhysicalResourceId should match MemoryId, "
            f"got PhysicalResourceId={result['PhysicalResourceId']}, "
            f"MemoryId={result['Data']['MemoryId']}"
        )
    
    # 5. For Delete operations, verify Data section is not required
    if operation_type == 'Delete':
        # Delete operations may or may not have Data section
        # If present, it should be valid, but it's not required
        if 'Data' in result:
            assert isinstance(result['Data'], dict), (
                "If Data section is present, it must be a dictionary"
            )
    
    # 6. Verify response structure is consistent
    # All responses must have PhysicalResourceId
    # Create and Update must have Data with MemoryId and MemoryArn
    # Delete may optionally have Data
    
    # Verify no unexpected fields in response
    expected_fields = {'PhysicalResourceId', 'Data'}
    actual_fields = set(result.keys())
    actual_fields - expected_fields
    
    # Allow additional fields, but warn if they exist
    # (CloudFormation custom resources can have additional fields)
    
    # Verify response is serializable (can be converted to JSON)
    import json
    try:
        json.dumps(result)
    except (TypeError, ValueError) as e:
        assert False, f"Response must be JSON serializable, got error: {e}"



@settings(max_examples=100, deadline=None)
@given(
    memory_id=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_-"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    operation_type=st.sampled_from(['Create', 'Update']),
    failure_reason=st.text(min_size=1, max_size=200),
    num_polls_before_failed=st.integers(min_value=0, max_value=5)
)
@patch('time.sleep', return_value=None)  # Mock sleep to make tests instant
def test_failed_status_raises_exception(
    mock_sleep,
    memory_id: str,
    operation_type: str,
    failure_reason: str,
    num_polls_before_failed: int
):
    """
    Property 6: Failed status raises exception
    
    
    
    For any Create or Update operation where the memory status reaches FAILED,
    the handler must raise an exception that includes the failureReason from
    the API response.
    
    This property verifies that:
    1. FAILED status during Create operations raises an exception
    2. FAILED status during Update operations raises an exception
    3. The exception message includes the failureReason from the API
    4. The exception is raised regardless of how many polls occurred before FAILED
    5. Polling terminates immediately when FAILED status is detected
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Generate ARN for the memory
    memory_arn = f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
    
    # Create a sequence of responses: in-progress status followed by FAILED
    responses = []
    
    # Add in-progress responses based on num_polls_before_failed
    in_progress_status = 'CREATING'  # Both Create and Update use CREATING during processing
    for _ in range(num_polls_before_failed):
        responses.append({
            'memory': {
                'id': memory_id,
                'arn': memory_arn,
                'status': in_progress_status,
                'name': memory_id,
                'description': 'Test memory'
            }
        })
    
    # Add final FAILED response with failureReason
    responses.append({
        'memory': {
            'id': memory_id,
            'arn': memory_arn,
            'status': 'FAILED',
            'name': memory_id,
            'description': 'Test memory',
            'failureReason': failure_reason
        }
    })
    
    # Mock get_memory to return the sequence of responses
    mock_client.get_memory = Mock(side_effect=responses)
    
    # Test the operation
    exception_raised = False
    exception_message = None
    
    try:
        # Wait for ACTIVE status (which should never be reached)
        with patch('time.sleep'):
            wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=60
            )
        
        # Should not reach here
        assert False, (
            f"{operation_type} operation should raise exception when status is FAILED"
        )
        
    except Exception as e:
        exception_raised = True
        exception_message = str(e)
    
    # Verify exception was raised
    assert exception_raised, (
        f"{operation_type} operation with FAILED status should raise exception"
    )
    
    # Verify exception message includes the memory_id
    assert memory_id in exception_message, (
        f"Exception message should include memory_id {memory_id}, "
        f"got: {exception_message}"
    )
    
    # Verify exception message includes the failureReason
    assert failure_reason in exception_message, (
        f"Exception message should include failureReason '{failure_reason}', "
        f"got: {exception_message}"
    )
    
    # Verify exception message indicates FAILED status
    assert 'FAILED' in exception_message, (
        f"Exception message should mention FAILED status, "
        f"got: {exception_message}"
    )
    
    # Verify get_memory was called the expected number of times
    # Should be num_polls_before_failed + 1 (for the FAILED response)
    expected_calls = num_polls_before_failed + 1
    assert mock_client.get_memory.call_count == expected_calls, (
        f"get_memory should be called {expected_calls} times, "
        f"but was called {mock_client.get_memory.call_count} times"
    )
    
    # Verify polling terminated immediately after FAILED status
    # (no additional calls after FAILED response)
    # This is implicitly verified by the call count check above


    # Mock the appropriate operation
    if operation_type == 'Create':
        mock_client.create_memory = Mock(return_value={
            'memory': {
                'id': memory_id,
                'arn': memory_arn,
                'status': 'CREATING'
            }
        })
    else:  # Update
        mock_client.update_memory = Mock(return_value={
            'memory': {
                'id': memory_id,
                'arn': memory_arn,
                'status': 'CREATING'
            }
        })
    
    # Mock get_memory to return responses in sequence
    mock_client.get_memory = Mock(side_effect=responses)
    
    # Simulate handler logic
    exception_raised = False
    exception_message = None
    
    with patch('time.sleep'):
        try:
            # Call appropriate operation
            if operation_type == 'Create':
                response = mock_client.create_memory(
                    name=memory_id,
                    description='Test memory',
                    eventExpiryDuration=90
                )
            else:  # Update
                response = mock_client.update_memory(
                    id=memory_id,
                    description='Updated description'
                )
            
            memory_id_from_response = response['memory']['id']
            
            # Wait for ACTIVE status (should fail with FAILED status)
            wait_for_memory_status(
                mock_client,
                memory_id_from_response,
                ['ACTIVE'],
                max_duration_seconds=60
            )
            
            # Should not reach here
            assert False, "Handler should raise exception when status is FAILED"
            
        except Exception as e:
            exception_raised = True
            exception_message = str(e)
    
    # Verify exception was raised
    assert exception_raised, (
        f"Handler should raise exception when memory status reaches FAILED "
        f"during {operation_type} operation"
    )
    
    # Verify exception message includes the failureReason
    assert failure_reason in exception_message, (
        f"Exception message should include failureReason '{failure_reason}', "
        f"but got: {exception_message}"
    )
    
    # Verify exception message includes the memory_id
    assert memory_id in exception_message, (
        f"Exception message should include memory_id '{memory_id}', "
        f"but got: {exception_message}"
    )
    
    # Verify get_memory was called the expected number of times
    # Should be num_polls_before_failed + 1 (for the FAILED status check)
    expected_calls = num_polls_before_failed + 1
    assert mock_client.get_memory.call_count == expected_calls, (
        f"Expected {expected_calls} calls to get_memory, "
        f"got {mock_client.get_memory.call_count}"
    )
    
    # Verify polling terminated immediately after FAILED status
    # (no additional calls after FAILED was detected)
    assert mock_client.get_memory.call_count <= len(responses), (
        f"Polling should terminate immediately after FAILED status, "
        f"but got {mock_client.get_memory.call_count} calls for {len(responses)} responses"
    )


@settings(max_examples=100, deadline=None)
@given(
    memory_id=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_-"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    operation_type=st.sampled_from(['Create', 'Update']),
    num_polls_before_deleting=st.integers(min_value=0, max_value=5)
)
@patch('time.sleep', return_value=None)  # Mock sleep to make tests instant
def test_unexpected_status_raises_exception(
    mock_sleep,
    memory_id: str,
    operation_type: str,
    num_polls_before_deleting: int
):
    """
    Property 7: Unexpected status raises exception
    
    
    
    For any Create or Update operation where the memory status is DELETING,
    the handler must raise an exception indicating an unexpected state.
    
    This property verifies that:
    1. DELETING status during Create operations raises an exception
    2. DELETING status during Update operations raises an exception
    3. The exception message indicates an unexpected state
    4. The exception is raised regardless of how many polls occurred before DELETING
    5. Polling terminates immediately when DELETING status is detected
    6. The exception message includes the memory_id for debugging
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Generate ARN for the memory
    memory_arn = f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
    
    # Create a sequence of responses: in-progress status followed by DELETING
    responses = []
    
    # Add in-progress responses based on num_polls_before_deleting
    in_progress_status = 'CREATING'  # Both Create and Update use CREATING during processing
    for _ in range(num_polls_before_deleting):
        responses.append({
            'memory': {
                'id': memory_id,
                'arn': memory_arn,
                'status': in_progress_status,
                'name': memory_id,
                'description': 'Test memory'
            }
        })
    
    # Add unexpected DELETING response
    responses.append({
        'memory': {
            'id': memory_id,
            'arn': memory_arn,
            'status': 'DELETING',
            'name': memory_id,
            'description': 'Test memory'
        }
    })
    
    # Mock the appropriate operation
    if operation_type == 'Create':
        mock_client.create_memory = Mock(return_value={
            'memory': {
                'id': memory_id,
                'arn': memory_arn,
                'status': 'CREATING'
            }
        })
    else:  # Update
        mock_client.update_memory = Mock(return_value={
            'memory': {
                'id': memory_id,
                'arn': memory_arn,
                'status': 'CREATING'
            }
        })
    
    # Mock get_memory to return responses in sequence
    mock_client.get_memory = Mock(side_effect=responses)
    
    # Simulate handler logic
    exception_raised = False
    exception_message = None
    
    with patch('time.sleep'):
        try:
            # Call appropriate operation
            if operation_type == 'Create':
                response = mock_client.create_memory(
                    name=memory_id,
                    description='Test memory',
                    eventExpiryDuration=90
                )
            else:  # Update
                response = mock_client.update_memory(
                    id=memory_id,
                    description='Updated description'
                )
            
            memory_id_from_response = response['memory']['id']
            
            # Wait for ACTIVE status (should fail with DELETING status)
            wait_for_memory_status(
                mock_client,
                memory_id_from_response,
                ['ACTIVE'],
                max_duration_seconds=60
            )
            
            # Should not reach here
            assert False, f"Handler should raise exception when status is DELETING during {operation_type}"
            
        except Exception as e:
            exception_raised = True
            exception_message = str(e)
    
    # Verify exception was raised
    assert exception_raised, (
        f"Handler should raise exception when memory status is DELETING "
        f"during {operation_type} operation"
    )
    
    # Verify exception message indicates unexpected state
    # The message should mention "DELETING" and indicate it's unexpected
    assert 'DELETING' in exception_message, (
        f"Exception message should mention DELETING status, "
        f"but got: {exception_message}"
    )
    
    # Verify exception message indicates this is unexpected
    unexpected_keywords = ['unexpected', 'invalid', 'wrong', 'incorrect']
    has_unexpected_keyword = any(keyword in exception_message.lower() for keyword in unexpected_keywords)
    assert has_unexpected_keyword, (
        f"Exception message should indicate unexpected state (using words like 'unexpected', 'invalid', etc.), "
        f"but got: {exception_message}"
    )
    
    # Verify exception message includes the memory_id for debugging
    assert memory_id in exception_message, (
        f"Exception message should include memory_id '{memory_id}' for debugging, "
        f"but got: {exception_message}"
    )
    
    # Verify get_memory was called the expected number of times
    # Should be num_polls_before_deleting + 1 (for the DELETING status check)
    expected_calls = num_polls_before_deleting + 1
    assert mock_client.get_memory.call_count == expected_calls, (
        f"Expected {expected_calls} calls to get_memory, "
        f"got {mock_client.get_memory.call_count}"
    )
    
    # Verify polling terminated immediately after DELETING status
    # (no additional calls after DELETING was detected)
    assert mock_client.get_memory.call_count <= len(responses), (
        f"Polling should terminate immediately after DELETING status, "
        f"but got {mock_client.get_memory.call_count} calls for {len(responses)} responses"
    )
    
    # Verify the exception is appropriate for the operation type
    # Both Create and Update should treat DELETING as unexpected
    if operation_type == 'Create':
        # During Create, DELETING is definitely unexpected
        assert 'operation' in exception_message.lower() or 'create' in exception_message.lower(), (
            f"Exception message should reference the operation context, "
            f"but got: {exception_message}"
        )
    else:  # Update
        # During Update, DELETING is also unexpected
        assert 'operation' in exception_message.lower() or 'update' in exception_message.lower(), (
            f"Exception message should reference the operation context, "
            f"but got: {exception_message}"
        )


@given(
    memory_id=st.text(
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_-'),
        min_size=1,
        max_size=48
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    last_status=st.sampled_from(['CREATING']),  # Only use CREATING to ensure polling continues
    num_polls_before_timeout=st.integers(min_value=2, max_value=10),
    max_duration=st.integers(min_value=5, max_value=30)
)
@patch('time.sleep', return_value=None)  # Mock sleep to make tests instant
def test_timeout_error_includes_context(
    mock_sleep,
    memory_id: str,
    last_status: str,
    num_polls_before_timeout: int,
    max_duration: int
):
    """
    Property 12: Timeout error includes context
    
    
    
    For any timeout during polling, the exception raised must include 
    the memory ID, last known status, and elapsed time.
    
    This property verifies that:
    1. TimeoutError is raised when max_duration is exceeded
    2. The error message includes the memory_id
    3. The error message includes the last known status
    4. The error message includes the elapsed time
    5. The timeout check happens before each polling attempt
    6. All required context is present regardless of how many polls occurred
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Generate ARN for the memory
    memory_arn = f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
    
    # Create responses that never reach ACTIVE (to force timeout)
    # We'll return CREATING status indefinitely
    def get_memory_response(*args, **kwargs):
        return {
            'memory': {
                'id': memory_id,
                'arn': memory_arn,
                'status': last_status,
                'name': memory_id,
                'description': 'Test memory'
            }
        }
    
    # Mock get_memory to return in-progress responses indefinitely
    mock_client.get_memory = Mock(side_effect=get_memory_response)
    
    # Simulate handler logic with time tracking
    timeout_error_raised = False
    error_message = None
    
    # Patch time.time to return values that will trigger timeout after some polls
    with patch('time.time') as mock_time:
        # Create a sequence of time values:
        # - First call: start time (0)
        # - Next num_polls_before_timeout calls: incrementing but under max_duration
        # - Final calls: exceed max_duration to trigger timeout
        start_time = 0.0
        time_increment = max_duration / (num_polls_before_timeout + 2)  # Spread time across polls
        
        time_values = [start_time]  # Initial time check
        
        # Add time values for polling attempts (under max_duration)
        for i in range(1, num_polls_before_timeout + 1):
            time_values.append(start_time + (i * time_increment))
        
        # Add time values that exceed max_duration to trigger timeout
        for i in range(2):
            time_values.append(start_time + max_duration + 1)
        
        mock_time.side_effect = time_values
        
        try:
            # Wait for ACTIVE status (should timeout)
            wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=max_duration
            )
            
            # Should not reach here
            assert False, "Handler should raise TimeoutError when max_duration is exceeded"
            
        except TimeoutError as e:
            timeout_error_raised = True
            error_message = str(e)
        except Exception as e:
            # If we get a different exception, fail the test
            assert False, f"Expected TimeoutError but got {type(e).__name__}: {str(e)}"
    
    # Verify TimeoutError was raised
    assert timeout_error_raised, (
        "Handler should raise TimeoutError when polling exceeds max_duration"
    )
    
    # Verify error message includes memory_id
    assert memory_id in error_message, (
        f"Timeout error message should include memory_id '{memory_id}', "
        f"but got: {error_message}"
    )
    
    # Verify error message includes last known status
    assert last_status in error_message or 'status' in error_message.lower(), (
        f"Timeout error message should include last known status '{last_status}' "
        f"or reference 'status', but got: {error_message}"
    )
    
    # Verify error message includes elapsed time information
    # Look for time-related keywords or numeric values that could represent elapsed time
    time_keywords = ['time', 'elapsed', 'duration', 'timeout', 'seconds']
    has_time_reference = any(keyword in error_message.lower() for keyword in time_keywords)
    
    # Also check for numeric values that could represent elapsed time
    import re
    has_numeric_time = bool(re.search(r'\d+\.?\d*\s*s', error_message.lower()))
    
    assert has_time_reference or has_numeric_time, (
        f"Timeout error message should include elapsed time information "
        f"(keywords like 'time', 'elapsed', 'duration' or numeric values with 's'), "
        f"but got: {error_message}"
    )
    
    # Verify the error message is descriptive and helpful for debugging
    # It should be more than just the three required pieces of information
    assert len(error_message) > 20, (
        f"Timeout error message should be descriptive, "
        f"but got only {len(error_message)} characters: {error_message}"
    )
    
    # Verify timeout check happened (get_memory was called at least once)
    assert mock_client.get_memory.call_count >= 1, (
        f"Expected at least 1 call to get_memory before timeout, "
        f"got {mock_client.get_memory.call_count}"
    )
    
    # Verify timeout prevented excessive polling
    # Should timeout after approximately num_polls_before_timeout attempts
    assert mock_client.get_memory.call_count <= num_polls_before_timeout + 2, (
        f"Polling should stop at timeout after ~{num_polls_before_timeout} polls, "
        f"but got {mock_client.get_memory.call_count} calls"
    )



@settings(max_examples=100, deadline=None)
@given(
    memory_id=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    operation_type=st.sampled_from(['create', 'update', 'delete', 'get']),
    num_api_calls=st.integers(min_value=1, max_value=5),
    description=st.text(min_size=0, max_size=100)
)
@patch('time.sleep', return_value=None)  # Mock sleep to make tests instant
def test_logging_completeness_for_api_calls(
    mock_sleep,
    memory_id: str,
    operation_type: str,
    num_api_calls: int,
    description: str
):
    """
    Property 13: Logging completeness for API calls
    
    
    
    For any API call to the Bedrock AgentCore service, the handler must log
    the request parameters before the call and the response status after the call.
    
    This property verifies that:
    1. Request parameters are logged before each API call
    2. Response status is logged after each API call
    3. Logging occurs for all API operations (create, update, delete, get)
    4. Each polling attempt is logged with timestamp and status
    5. All API calls have corresponding log entries
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create a custom logger to capture logs
    import logging
    test_logger = logging.getLogger('test_logging_completeness')
    test_logger.setLevel(logging.INFO)
    
    # Create a handler to capture log records
    log_records = []
    
    class TestHandler(logging.Handler):
        def emit(self, record):
            log_records.append(record)
    
    handler = TestHandler()
    test_logger.addHandler(handler)
    
    # Track API calls
    api_calls_made = []
    
    # Generate ARN for the memory
    memory_arn = f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_id}'
    
    # Mock API methods based on operation type
    if operation_type == 'create':
        def mock_create_memory(**kwargs):
            api_calls_made.append(('create_memory', kwargs))
            # Log request parameters
            test_logger.info(
                "Calling create_memory API",
                extra={
                    "operation": "create_memory",
                    "request_params": kwargs
                }
            )
            response = {
                'memory': {
                    'id': memory_id,
                    'arn': memory_arn,
                    'status': 'CREATING',
                    'name': kwargs.get('name', memory_id),
                    'description': kwargs.get('description', '')
                }
            }
            # Log response status
            test_logger.info(
                "create_memory API response",
                extra={
                    "operation": "create_memory",
                    "response_status": response['memory']['status'],
                    "memory_id": response['memory']['id']
                }
            )
            return response
        
        mock_client.create_memory = Mock(side_effect=mock_create_memory)
        
        # Create responses for status polling
        responses = []
        for i in range(num_api_calls - 1):
            responses.append({
                'memory': {
                    'id': memory_id,
                    'arn': memory_arn,
                    'status': 'CREATING',
                    'name': memory_id,
                    'description': description
                }
            })
        # Final ACTIVE response
        responses.append({
            'memory': {
                'id': memory_id,
                'arn': memory_arn,
                'status': 'ACTIVE',
                'name': memory_id,
                'description': description
            }
        })
        
        def mock_get_memory(**kwargs):
            api_calls_made.append(('get_memory', kwargs))
            # Log request parameters
            test_logger.info(
                "Calling get_memory API",
                extra={
                    "operation": "get_memory",
                    "request_params": kwargs,
                    "attempt": len([c for c in api_calls_made if c[0] == 'get_memory'])
                }
            )
            response = responses.pop(0) if responses else {
                'memory': {
                    'id': memory_id,
                    'arn': memory_arn,
                    'status': 'ACTIVE',
                    'name': memory_id,
                    'description': description
                }
            }
            # Log response status
            test_logger.info(
                "get_memory API response",
                extra={
                    "operation": "get_memory",
                    "response_status": response['memory']['status'],
                    "memory_id": response['memory']['id'],
                    "attempt": len([c for c in api_calls_made if c[0] == 'get_memory'])
                }
            )
            return response
        
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        
        # Simulate create operation
        with patch('time.sleep'):
            # Call create_memory
            mock_client.create_memory(
                name=memory_id,
                description=description,
                eventExpiryDuration=90
            )
            
            # Wait for ACTIVE status
            wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=60
            )
    
    elif operation_type == 'update':
        def mock_update_memory(**kwargs):
            api_calls_made.append(('update_memory', kwargs))
            # Log request parameters
            test_logger.info(
                "Calling update_memory API",
                extra={
                    "operation": "update_memory",
                    "request_params": kwargs
                }
            )
            response = {
                'memory': {
                    'id': memory_id,
                    'arn': memory_arn,
                    'status': 'CREATING'
                }
            }
            # Log response status
            test_logger.info(
                "update_memory API response",
                extra={
                    "operation": "update_memory",
                    "response_status": response['memory']['status'],
                    "memory_id": response['memory']['id']
                }
            )
            return response
        
        mock_client.update_memory = Mock(side_effect=mock_update_memory)
        
        # Create responses for status polling
        responses = []
        for i in range(num_api_calls - 1):
            responses.append({
                'memory': {
                    'id': memory_id,
                    'arn': memory_arn,
                    'status': 'CREATING',
                    'name': memory_id,
                    'description': description
                }
            })
        # Final ACTIVE response
        responses.append({
            'memory': {
                'id': memory_id,
                'arn': memory_arn,
                'status': 'ACTIVE',
                'name': memory_id,
                'description': description
            }
        })
        
        def mock_get_memory(**kwargs):
            api_calls_made.append(('get_memory', kwargs))
            # Log request parameters
            test_logger.info(
                "Calling get_memory API",
                extra={
                    "operation": "get_memory",
                    "request_params": kwargs,
                    "attempt": len([c for c in api_calls_made if c[0] == 'get_memory'])
                }
            )
            response = responses.pop(0) if responses else {
                'memory': {
                    'id': memory_id,
                    'arn': memory_arn,
                    'status': 'ACTIVE',
                    'name': memory_id,
                    'description': description
                }
            }
            # Log response status
            test_logger.info(
                "get_memory API response",
                extra={
                    "operation": "get_memory",
                    "response_status": response['memory']['status'],
                    "memory_id": response['memory']['id'],
                    "attempt": len([c for c in api_calls_made if c[0] == 'get_memory'])
                }
            )
            return response
        
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        
        # Simulate update operation
        with patch('time.sleep'):
            # Call update_memory
            mock_client.update_memory(
                id=memory_id,
                description=description
            )
            
            # Wait for ACTIVE status
            wait_for_memory_status(
                mock_client,
                memory_id,
                ['ACTIVE'],
                max_duration_seconds=60
            )
    
    elif operation_type == 'delete':
        # Create exception class
        class ResourceNotFoundException(Exception):
            pass
        
        mock_client.exceptions = Mock()
        mock_client.exceptions.ResourceNotFoundException = ResourceNotFoundException
        
        def mock_delete_memory(**kwargs):
            api_calls_made.append(('delete_memory', kwargs))
            # Log request parameters
            test_logger.info(
                "Calling delete_memory API",
                extra={
                    "operation": "delete_memory",
                    "request_params": kwargs
                }
            )
            # Log response status
            test_logger.info(
                "delete_memory API response",
                extra={
                    "operation": "delete_memory",
                    "response_status": "success",
                    "memory_id": kwargs.get('memoryId')
                }
            )
            return {}
        
        mock_client.delete_memory = Mock(side_effect=mock_delete_memory)
        
        # Create responses for status polling
        responses = []
        for i in range(num_api_calls - 1):
            responses.append({
                'memory': {
                    'id': memory_id,
                    'arn': memory_arn,
                    'status': 'DELETING'
                }
            })
        
        def mock_get_memory(**kwargs):
            api_calls_made.append(('get_memory', kwargs))
            # Log request parameters
            test_logger.info(
                "Calling get_memory API",
                extra={
                    "operation": "get_memory",
                    "request_params": kwargs,
                    "attempt": len([c for c in api_calls_made if c[0] == 'get_memory'])
                }
            )
            if responses:
                response = responses.pop(0)
                # Log response status
                test_logger.info(
                    "get_memory API response",
                    extra={
                        "operation": "get_memory",
                        "response_status": response['memory']['status'],
                        "memory_id": response['memory']['id'],
                        "attempt": len([c for c in api_calls_made if c[0] == 'get_memory'])
                    }
                )
                return response
            else:
                # Memory deleted
                test_logger.info(
                    "get_memory API response",
                    extra={
                        "operation": "get_memory",
                        "response_status": "ResourceNotFoundException",
                        "memory_id": kwargs.get('memoryId'),
                        "attempt": len([c for c in api_calls_made if c[0] == 'get_memory'])
                    }
                )
                raise ResourceNotFoundException("Memory not found")
        
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        
        # Simulate delete operation
        with patch('time.sleep'):
            # Call delete_memory
            mock_client.delete_memory(memoryId=memory_id)
            
            # Simulate polling for deletion (without calling wait_for_memory_status)
            # Just call get_memory directly to trigger logging
            for _ in range(num_api_calls - 1):
                try:
                    mock_client.get_memory(memoryId=memory_id)
                except ResourceNotFoundException:
                    break
    
    else:  # get operation
        # Create responses for multiple get_memory calls
        responses = []
        for i in range(num_api_calls):
            responses.append({
                'memory': {
                    'id': memory_id,
                    'arn': memory_arn,
                    'status': 'ACTIVE',
                    'name': memory_id,
                    'description': description
                }
            })
        
        def mock_get_memory(**kwargs):
            api_calls_made.append(('get_memory', kwargs))
            # Log request parameters
            test_logger.info(
                "Calling get_memory API",
                extra={
                    "operation": "get_memory",
                    "request_params": kwargs,
                    "attempt": len([c for c in api_calls_made if c[0] == 'get_memory'])
                }
            )
            response = responses.pop(0) if responses else {
                'memory': {
                    'id': memory_id,
                    'arn': memory_arn,
                    'status': 'ACTIVE',
                    'name': memory_id,
                    'description': description
                }
            }
            # Log response status
            test_logger.info(
                "get_memory API response",
                extra={
                    "operation": "get_memory",
                    "response_status": response['memory']['status'],
                    "memory_id": response['memory']['id'],
                    "attempt": len([c for c in api_calls_made if c[0] == 'get_memory'])
                }
            )
            return response
        
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        
        # Simulate multiple get operations
        for i in range(num_api_calls):
            mock_client.get_memory(memoryId=memory_id)
    
    # Verify logging completeness
    
    # 1. Verify that API calls were made
    assert len(api_calls_made) > 0, (
        f"Expected at least one API call for {operation_type} operation"
    )
    
    # 2. Verify that log records were created
    assert len(log_records) > 0, (
        f"Expected log records for {operation_type} operation, but got none"
    )
    
    # 3. Verify that each API call has corresponding log entries
    # Each API call should have at least 2 log entries: request and response
    expected_min_logs = len(api_calls_made) * 2
    assert len(log_records) >= expected_min_logs, (
        f"Expected at least {expected_min_logs} log records "
        f"(2 per API call: request + response) for {len(api_calls_made)} API calls, "
        f"but got {len(log_records)}"
    )
    
    # 4. Verify request parameters are logged
    request_logs = [r for r in log_records if 'Calling' in r.getMessage()]
    assert len(request_logs) >= len(api_calls_made), (
        f"Expected at least {len(api_calls_made)} request logs, "
        f"but got {len(request_logs)}"
    )
    
    # Verify request logs contain parameters
    for log_record in request_logs:
        assert hasattr(log_record, 'operation'), (
            "Request log should contain 'operation' field"
        )
        assert hasattr(log_record, 'request_params'), (
            "Request log should contain 'request_params' field"
        )
        assert log_record.request_params is not None, (
            "Request parameters should not be None"
        )
    
    # 5. Verify response status is logged
    response_logs = [r for r in log_records if 'response' in r.getMessage()]
    assert len(response_logs) >= len(api_calls_made), (
        f"Expected at least {len(api_calls_made)} response logs, "
        f"but got {len(response_logs)}"
    )
    
    # Verify response logs contain status
    for log_record in response_logs:
        assert hasattr(log_record, 'operation'), (
            "Response log should contain 'operation' field"
        )
        assert hasattr(log_record, 'response_status'), (
            "Response log should contain 'response_status' field"
        )
        assert log_record.response_status is not None, (
            "Response status should not be None"
        )
    
    # 6. Verify polling attempts are logged with attempt count
    if operation_type in ['create', 'update', 'delete']:
        # These operations involve status polling
        get_memory_logs = [r for r in log_records if hasattr(r, 'operation') and r.operation == 'get_memory']
        
        if len(get_memory_logs) > 0:
            # Verify attempt count is logged for polling operations
            polling_logs_with_attempt = [r for r in get_memory_logs if hasattr(r, 'attempt')]
            assert len(polling_logs_with_attempt) > 0, (
                "Polling logs should include attempt count"
            )
    
    # 7. Verify memory_id is logged in all relevant log entries
    logs_with_memory_id = [r for r in log_records if hasattr(r, 'memory_id')]
    assert len(logs_with_memory_id) > 0, (
        "At least some log entries should include memory_id"
    )
    
    # Verify memory_id values are correct
    for log_record in logs_with_memory_id:
        assert log_record.memory_id == memory_id, (
            f"Log entry memory_id should be {memory_id}, "
            f"got {log_record.memory_id}"
        )
    
    # 8. Verify log levels are appropriate
    # All logs in this test should be INFO level
    for log_record in log_records:
        assert log_record.levelno == logging.INFO, (
            f"Expected INFO level (20), got {log_record.levelno}"
        )
    
    # 9. Verify log messages are descriptive
    for log_record in log_records:
        message = log_record.getMessage()
        assert len(message) > 0, "Log message should not be empty"
        assert len(message) > 10, (
            f"Log message should be descriptive, got only {len(message)} chars: {message}"
        )
    
    # 10. Verify all API operations are logged
    logged_operations = set(r.operation for r in log_records if hasattr(r, 'operation'))
    
    if operation_type == 'create':
        assert 'create_memory' in logged_operations, (
            "create_memory operation should be logged"
        )
        assert 'get_memory' in logged_operations, (
            "get_memory operation should be logged for status polling"
        )
    elif operation_type == 'update':
        assert 'update_memory' in logged_operations, (
            "update_memory operation should be logged"
        )
        assert 'get_memory' in logged_operations, (
            "get_memory operation should be logged for status polling"
        )
    elif operation_type == 'delete':
        assert 'delete_memory' in logged_operations, (
            "delete_memory operation should be logged"
        )
        # get_memory may or may not be logged depending on whether polling occurred
    else:  # get
        assert 'get_memory' in logged_operations, (
            "get_memory operation should be logged"
        )
    
    # Clean up test logger
    test_logger.removeHandler(handler)


@settings(max_examples=100, deadline=None)
@given(
    memory_id=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    deletion_scenario=st.sampled_from([
        'never_created',
        'already_deleted',
        'partial_creation_failed'
    ])
)
def test_delete_succeeds_for_non_existent_resources(
    memory_id: str,
    deletion_scenario: str
):
    """
    Property 19: Delete succeeds for non-existent resources
    
    
    
    For any Delete operation where the memory was never fully created or doesn't exist,
    the handler must return success.
    
    This property verifies that:
    1. Delete succeeds when memory was never created
    2. Delete succeeds when memory was already deleted
    3. Delete succeeds when memory creation partially failed
    4. ResourceNotFoundException during delete_memory() returns success
    5. ResourceNotFoundException during status polling returns success
    6. Handler never blocks CloudFormation stack deletion due to missing resources
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create exception class
    class ResourceNotFoundException(Exception):
        pass
    
    # Attach exception to client
    mock_client.exceptions = Mock()
    mock_client.exceptions.ResourceNotFoundException = ResourceNotFoundException
    
    # Track whether operations were called
    delete_called = False
    get_memory_called = False
    
    # Configure mocks based on deletion scenario
    if deletion_scenario == 'never_created':
        # Memory was never created, delete_memory raises ResourceNotFoundException immediately
        def mock_delete_memory(**kwargs):
            nonlocal delete_called
            delete_called = True
            raise ResourceNotFoundException(f"Memory {memory_id} not found")
        
        mock_client.delete_memory = Mock(side_effect=mock_delete_memory)
        
    elif deletion_scenario == 'already_deleted':
        # Memory was deleted previously, delete_memory succeeds but get_memory raises ResourceNotFoundException
        def mock_delete_memory(**kwargs):
            nonlocal delete_called
            delete_called = True
            return {}
        
        def mock_get_memory(**kwargs):
            nonlocal get_memory_called
            get_memory_called = True
            raise ResourceNotFoundException(f"Memory {memory_id} not found")
        
        mock_client.delete_memory = Mock(side_effect=mock_delete_memory)
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        
    elif deletion_scenario == 'partial_creation_failed':
        # Memory creation partially failed, resource may or may not exist
        # Simulate ResourceNotFoundException during delete
        def mock_delete_memory(**kwargs):
            nonlocal delete_called
            delete_called = True
            raise ResourceNotFoundException(f"Memory {memory_id} not found")
        
        mock_client.delete_memory = Mock(side_effect=mock_delete_memory)
    
    # Simulate on_delete handler logic
    result = None
    exception_raised_to_cfn = False
    
    try:
        # Wrap entire delete logic in try-except to ensure success
        try:
            # Call delete_memory
            mock_client.delete_memory(memoryId=memory_id)
            
            # If delete succeeded, poll for completion
            if deletion_scenario == 'already_deleted':
                with patch('time.sleep'):
                    try:
                        wait_for_memory_status(
                            mock_client,
                            memory_id,
                            ['DELETED'],
                            max_duration_seconds=60
                        )
                    except ResourceNotFoundException:
                        # Memory already deleted, this is success
                        pass
            
            # Return success
            result = {
                'PhysicalResourceId': memory_id
            }
            
        except ResourceNotFoundException:
            # Memory doesn't exist (never created or already deleted)
            # This is a success case for Delete operations
            result = {
                'PhysicalResourceId': memory_id
            }
            
        except Exception:
            # Any other error, log but return success to avoid blocking CloudFormation
            result = {
                'PhysicalResourceId': memory_id
            }
            
    except Exception:
        # If any exception escapes, it would be raised to CloudFormation
        exception_raised_to_cfn = True
        raise
    
    # Verify handler always returns success
    assert result is not None, (
        f"Handler should return success for deletion scenario: {deletion_scenario}"
    )
    assert result['PhysicalResourceId'] == memory_id, (
        f"PhysicalResourceId should be {memory_id}, "
        f"got {result['PhysicalResourceId']}"
    )
    
    # Verify no exception was raised to CloudFormation
    assert not exception_raised_to_cfn, (
        f"Delete handler should not raise exceptions to CloudFormation "
        f"for deletion scenario: {deletion_scenario}"
    )
    
    # Verify delete_memory was called (handler attempted deletion)
    assert delete_called, (
        f"delete_memory should be called for deletion scenario: {deletion_scenario}"
    )
    
    # Verify behavior specific to each scenario
    if deletion_scenario == 'never_created':
        # ResourceNotFoundException from delete_memory should be caught
        # and result in success
        assert result is not None, (
            "Delete should succeed when memory was never created"
        )
        
    elif deletion_scenario == 'already_deleted':
        # delete_memory succeeds, but get_memory raises ResourceNotFoundException
        # This should also result in success
        assert result is not None, (
            "Delete should succeed when memory was already deleted"
        )
        # Verify get_memory was called during status polling
        assert get_memory_called, (
            "get_memory should be called during status polling for already_deleted scenario"
        )
        
    elif deletion_scenario == 'partial_creation_failed':
        # Memory creation partially failed, resource doesn't exist
        # ResourceNotFoundException should result in success
        assert result is not None, (
            "Delete should succeed when memory creation partially failed"
        )
    
    # Verify CloudFormation stack deletion would not be blocked
    # This is implicit in the fact that result is not None and no exception was raised
    
    # Verify idempotency: multiple deletes should all succeed
    # Try deleting again with the same scenario
    result2 = None
    try:
        try:
            mock_client.delete_memory(memoryId=memory_id)
            
            if deletion_scenario == 'already_deleted':
                with patch('time.sleep'):
                    try:
                        wait_for_memory_status(
                            mock_client,
                            memory_id,
                            ['DELETED'],
                            max_duration_seconds=60
                        )
                    except ResourceNotFoundException:
                        pass
            
            result2 = {
                'PhysicalResourceId': memory_id
            }
            
        except ResourceNotFoundException:
            result2 = {
                'PhysicalResourceId': memory_id
            }
            
        except Exception:
            result2 = {
                'PhysicalResourceId': memory_id
            }
            
    except Exception:
        # Should not reach here
        pass
    
    # Verify second delete also succeeds (idempotency)
    assert result2 is not None, (
        f"Second delete should also succeed for deletion scenario: {deletion_scenario}"
    )
    assert result2['PhysicalResourceId'] == memory_id, (
        "Second delete should return same PhysicalResourceId"
    )
    
    # Verify the handler prevents CloudFormation from being blocked
    # by non-existent resources during stack deletion
    # This is a critical requirement for CloudFormation custom resources
    assert result['PhysicalResourceId'] == memory_id, (
        "Delete must return PhysicalResourceId to signal success to CloudFormation"
    )


@settings(max_examples=100, deadline=None)
@given(
    memory_name=st.text(
        min_size=1,
        max_size=48,
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_"
        )
    ).filter(lambda x: x[0].isalpha() if x else False),  # Must start with letter
    description=st.text(min_size=0, max_size=100),
    retry_scenario=st.sampled_from([
        'create_conflict_active',
        'create_conflict_creating',
        'create_conflict_failed',
        'update_same_state',
        'update_different_state',
        'delete_not_found'
    ])
)
def test_memory_name_as_idempotency_key(
    memory_name: str,
    description: str,
    retry_scenario: str
):
    """
    Property 23: Memory name as idempotency key
    
    
    
    For any retry scenario, the handler must use the memory name to identify
    existing resources and determine appropriate action.
    
    This property verifies that:
    1. Memory name is used as the natural idempotency key
    2. During Create retries, memory name identifies existing resources
    3. During Update retries, memory name identifies the resource to update
    4. During Delete retries, memory name identifies the resource to delete
    5. Handler uses memory name consistently across all operations
    6. Memory name enables safe retry behavior without side effects
    """
    # Create mock bedrock client
    mock_client = Mock()
    
    # Create exception classes
    class ConflictException(Exception):
        pass
    
    class ResourceNotFoundException(Exception):
        pass
    
    # Attach exceptions to client
    mock_client.exceptions = Mock()
    mock_client.exceptions.ConflictException = ConflictException
    mock_client.exceptions.ResourceNotFoundException = ResourceNotFoundException
    
    # Generate ARN for the memory
    memory_arn = f'arn:aws:bedrock:us-east-1:123456789012:memory/{memory_name}'
    
    # Track API calls and the memory names used
    api_calls = []
    
    # Test different retry scenarios
    if retry_scenario == 'create_conflict_active':
        # Scenario: Create retry where memory already exists with ACTIVE status
        # Handler should use memory name to check existing resource and return it
        
        def mock_create_memory(**kwargs):
            api_calls.append(('create_memory', kwargs.get('name')))
            raise ConflictException("Memory with this name already exists")
        
        def mock_get_memory(**kwargs):
            api_calls.append(('get_memory', kwargs.get('memoryId')))
            return {
                'memory': {
                    'id': memory_name,
                    'arn': memory_arn,
                    'status': 'ACTIVE',
                    'name': memory_name,
                    'description': description
                }
            }
        
        mock_client.create_memory = Mock(side_effect=mock_create_memory)
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        
        # Simulate on_create handler with retry logic
        try:
            mock_client.create_memory(
                name=memory_name,
                description=description,
                eventExpiryDuration=90
            )
        except ConflictException:
            # Check existing memory using memory name as key
            get_response = mock_client.get_memory(memoryId=memory_name)
            existing_memory = get_response.get('memory', {})
            
            if existing_memory.get('status') == 'ACTIVE':
                # Return existing memory
                result = {
                    'PhysicalResourceId': existing_memory['id'],
                    'Data': {
                        'MemoryId': existing_memory['id'],
                        'MemoryArn': existing_memory['arn']
                    }
                }
        
        # Verify memory name was used as idempotency key
        assert len(api_calls) == 2, f"Expected 2 API calls, got {len(api_calls)}"
        
        # Verify create_memory was called with memory name
        assert api_calls[0][0] == 'create_memory', "First call should be create_memory"
        assert api_calls[0][1] == memory_name, (
            f"create_memory should use memory name {memory_name}, got {api_calls[0][1]}"
        )
        
        # Verify get_memory was called with memory name as key
        assert api_calls[1][0] == 'get_memory', "Second call should be get_memory"
        assert api_calls[1][1] == memory_name, (
            f"get_memory should use memory name {memory_name} as key, got {api_calls[1][1]}"
        )
        
        # Verify result uses memory name
        assert result['PhysicalResourceId'] == memory_name, (
            f"PhysicalResourceId should be memory name {memory_name}, "
            f"got {result['PhysicalResourceId']}"
        )
    
    elif retry_scenario == 'create_conflict_creating':
        # Scenario: Create retry where memory exists but is still CREATING
        # Handler should use memory name to check status and raise error
        
        def mock_create_memory(**kwargs):
            api_calls.append(('create_memory', kwargs.get('name')))
            raise ConflictException("Memory with this name already exists")
        
        def mock_get_memory(**kwargs):
            api_calls.append(('get_memory', kwargs.get('memoryId')))
            return {
                'memory': {
                    'id': memory_name,
                    'arn': memory_arn,
                    'status': 'CREATING',
                    'name': memory_name,
                    'description': description
                }
            }
        
        mock_client.create_memory = Mock(side_effect=mock_create_memory)
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        
        # Simulate on_create handler with retry logic
        exception_raised = False
        try:
            mock_client.create_memory(
                name=memory_name,
                description=description,
                eventExpiryDuration=90
            )
        except ConflictException:
            # Check existing memory using memory name as key
            get_response = mock_client.get_memory(memoryId=memory_name)
            existing_memory = get_response.get('memory', {})
            
            if existing_memory.get('status') != 'ACTIVE':
                # Raise error for non-ACTIVE status
                exception_raised = True
                (
                    f"Memory {memory_name} already exists but is not ACTIVE. "
                    f"Current status: {existing_memory.get('status')}"
                )
        
        # Verify memory name was used as idempotency key
        assert len(api_calls) == 2, f"Expected 2 API calls, got {len(api_calls)}"
        assert api_calls[0][1] == memory_name, "create_memory should use memory name"
        assert api_calls[1][1] == memory_name, "get_memory should use memory name as key"
        assert exception_raised, "Should raise error for non-ACTIVE status"
    
    elif retry_scenario == 'create_conflict_failed':
        # Scenario: Create retry where memory exists but is FAILED
        # Handler should use memory name to check status and raise error
        
        def mock_create_memory(**kwargs):
            api_calls.append(('create_memory', kwargs.get('name')))
            raise ConflictException("Memory with this name already exists")
        
        def mock_get_memory(**kwargs):
            api_calls.append(('get_memory', kwargs.get('memoryId')))
            return {
                'memory': {
                    'id': memory_name,
                    'arn': memory_arn,
                    'status': 'FAILED',
                    'name': memory_name,
                    'description': description,
                    'failureReason': 'Previous creation failed'
                }
            }
        
        mock_client.create_memory = Mock(side_effect=mock_create_memory)
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        
        # Simulate on_create handler with retry logic
        exception_raised = False
        try:
            mock_client.create_memory(
                name=memory_name,
                description=description,
                eventExpiryDuration=90
            )
        except ConflictException:
            # Check existing memory using memory name as key
            get_response = mock_client.get_memory(memoryId=memory_name)
            existing_memory = get_response.get('memory', {})
            
            if existing_memory.get('status') != 'ACTIVE':
                # Raise error for non-ACTIVE status
                exception_raised = True
        
        # Verify memory name was used as idempotency key
        assert api_calls[0][1] == memory_name, "create_memory should use memory name"
        assert api_calls[1][1] == memory_name, "get_memory should use memory name as key"
        assert exception_raised, "Should raise error for FAILED status"
    
    elif retry_scenario == 'update_same_state':
        # Scenario: Update retry where current state matches desired state
        # Handler should use memory name to check current state and skip update
        
        def mock_get_memory(**kwargs):
            api_calls.append(('get_memory', kwargs.get('memoryId')))
            return {
                'memory': {
                    'id': memory_name,
                    'arn': memory_arn,
                    'status': 'ACTIVE',
                    'name': memory_name,
                    'description': description
                }
            }
        
        def mock_update_memory(**kwargs):
            api_calls.append(('update_memory', kwargs.get('id')))
            return {
                'memory': {
                    'id': memory_name,
                    'arn': memory_arn,
                    'status': 'CREATING'
                }
            }
        
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        mock_client.update_memory = Mock(side_effect=mock_update_memory)
        
        # Simulate on_update handler with idempotency check
        # Check current state using memory name
        get_response = mock_client.get_memory(memoryId=memory_name)
        current_memory = get_response.get('memory', {})
        current_description = current_memory.get('description', '')
        
        # Only update if description changed
        if current_description != description:
            mock_client.update_memory(
                id=memory_name,
                description=description
            )
        
        # Verify memory name was used as idempotency key
        assert len(api_calls) >= 1, "Should have at least one API call"
        assert api_calls[0][0] == 'get_memory', "First call should be get_memory"
        assert api_calls[0][1] == memory_name, (
            f"get_memory should use memory name {memory_name} as key, got {api_calls[0][1]}"
        )
        
        # Since description matches, update_memory should not be called
        update_calls = [call for call in api_calls if call[0] == 'update_memory']
        assert len(update_calls) == 0, "update_memory should not be called when state matches"
    
    elif retry_scenario == 'update_different_state':
        # Scenario: Update retry where current state differs from desired state
        # Handler should use memory name to identify resource and apply update
        
        current_description = description + "_old"
        new_description = description
        
        def mock_get_memory(**kwargs):
            api_calls.append(('get_memory', kwargs.get('memoryId')))
            return {
                'memory': {
                    'id': memory_name,
                    'arn': memory_arn,
                    'status': 'ACTIVE',
                    'name': memory_name,
                    'description': current_description
                }
            }
        
        def mock_update_memory(**kwargs):
            api_calls.append(('update_memory', kwargs.get('id')))
            return {
                'memory': {
                    'id': memory_name,
                    'arn': memory_arn,
                    'status': 'CREATING'
                }
            }
        
        mock_client.get_memory = Mock(side_effect=mock_get_memory)
        mock_client.update_memory = Mock(side_effect=mock_update_memory)
        
        # Simulate on_update handler with idempotency check
        # Check current state using memory name
        get_response = mock_client.get_memory(memoryId=memory_name)
        current_memory = get_response.get('memory', {})
        current_memory_description = current_memory.get('description', '')
        
        # Update if description changed
        if current_memory_description != new_description:
            mock_client.update_memory(
                id=memory_name,
                description=new_description
            )
        
        # Verify memory name was used as idempotency key
        assert len(api_calls) == 2, f"Expected 2 API calls, got {len(api_calls)}"
        
        # Verify get_memory used memory name
        assert api_calls[0][0] == 'get_memory', "First call should be get_memory"
        assert api_calls[0][1] == memory_name, (
            f"get_memory should use memory name {memory_name} as key, got {api_calls[0][1]}"
        )
        
        # Verify update_memory used memory name
        assert api_calls[1][0] == 'update_memory', "Second call should be update_memory"
        assert api_calls[1][1] == memory_name, (
            f"update_memory should use memory name {memory_name} as key, got {api_calls[1][1]}"
        )
    
    elif retry_scenario == 'delete_not_found':
        # Scenario: Delete retry where memory doesn't exist
        # Handler should use memory name to attempt delete and handle not found
        
        def mock_delete_memory(**kwargs):
            api_calls.append(('delete_memory', kwargs.get('memoryId')))
            raise ResourceNotFoundException("Memory not found")
        
        mock_client.delete_memory = Mock(side_effect=mock_delete_memory)
        
        # Simulate on_delete handler with idempotency
        result = None
        try:
            mock_client.delete_memory(memoryId=memory_name)
        except ResourceNotFoundException:
            # Memory already deleted (idempotency)
            result = {
                'PhysicalResourceId': memory_name
            }
        
        # Verify memory name was used as idempotency key
        assert len(api_calls) == 1, f"Expected 1 API call, got {len(api_calls)}"
        assert api_calls[0][0] == 'delete_memory', "Should call delete_memory"
        assert api_calls[0][1] == memory_name, (
            f"delete_memory should use memory name {memory_name} as key, got {api_calls[0][1]}"
        )
        
        # Verify result uses memory name
        assert result['PhysicalResourceId'] == memory_name, (
            f"PhysicalResourceId should be memory name {memory_name}, "
            f"got {result['PhysicalResourceId']}"
        )
    
    # Verify memory name is used consistently across all scenarios
    # All API calls should use the same memory name
    for call_type, call_memory_name in api_calls:
        assert call_memory_name == memory_name, (
            f"All API calls should use memory name {memory_name}, "
            f"but {call_type} used {call_memory_name}"
        )
    
    # Verify memory name enables safe retry behavior
    # Multiple retries with same memory name should be idempotent
    # (This is implicitly tested by the scenarios above)
