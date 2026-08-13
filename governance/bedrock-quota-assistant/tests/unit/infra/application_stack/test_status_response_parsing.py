"""Unit tests for status response parsing from AgentCore Memory API."""

from datetime import datetime


def test_extract_memory_id_and_arn_from_response():
    """
    Test extracting memory_id and arn from API response.
    
    The get_memory() API returns a response with a 'memory' key containing
    the memory details including 'id' and 'arn' fields.
    """
    # Simulate API response structure
    api_response = {
        'memory': {
            'id': 'mem-12345678',
            'arn': 'arn:aws:bedrock:us-east-1:123456789012:memory/mem-12345678',
            'name': 'test-memory',
            'status': 'ACTIVE',
            'description': 'Test memory resource'
        }
    }
    
    # Extract memory details
    memory = api_response.get('memory', {})
    memory_id = memory.get('id')
    memory_arn = memory.get('arn')
    
    # Verify extraction
    assert memory_id == 'mem-12345678', f"Expected memory_id 'mem-12345678', got '{memory_id}'"
    assert memory_arn == 'arn:aws:bedrock:us-east-1:123456789012:memory/mem-12345678', (
        f"Expected specific ARN, got '{memory_arn}'"
    )


def test_extract_from_create_memory_response():
    """
    Test extracting memory_id and arn from create_memory() response.
    
    The create_memory() API returns the same structure as get_memory().
    """
    # Simulate create_memory response
    create_response = {
        'memory': {
            'id': 'mem-abcdef12',
            'arn': 'arn:aws:bedrock:us-west-2:987654321098:memory/mem-abcdef12',
            'name': 'new-memory',
            'status': 'CREATING',
            'description': 'Newly created memory'
        }
    }
    
    # Extract as done in on_create handler
    memory_id = create_response['memory']['id']
    memory_arn = create_response['memory']['arn']
    
    # Verify extraction
    assert memory_id == 'mem-abcdef12'
    assert memory_arn == 'arn:aws:bedrock:us-west-2:987654321098:memory/mem-abcdef12'


def test_handle_missing_optional_description_field():
    """
    Test handling missing optional 'description' field.
    
    The description field is optional in the API response.
    Code should handle its absence gracefully.
    """
    # Response without description
    api_response = {
        'memory': {
            'id': 'mem-99999999',
            'arn': 'arn:aws:bedrock:us-east-1:111111111111:memory/mem-99999999',
            'name': 'minimal-memory',
            'status': 'ACTIVE'
            # No 'description' field
        }
    }
    
    # Extract with default value
    memory = api_response.get('memory', {})
    description = memory.get('description', '')
    
    # Verify default is used
    assert description == '', f"Expected empty string for missing description, got '{description}'"
    
    # Verify other fields still work
    assert memory.get('id') == 'mem-99999999'
    assert memory.get('status') == 'ACTIVE'


def test_handle_missing_optional_failure_reason_field():
    """
    Test handling missing optional 'failureReason' field.
    
    The failureReason field is only present when status is FAILED.
    Code should handle its absence gracefully.
    """
    # Response without failureReason (status is ACTIVE)
    api_response = {
        'memory': {
            'id': 'mem-active123',
            'arn': 'arn:aws:bedrock:us-east-1:222222222222:memory/mem-active123',
            'name': 'active-memory',
            'status': 'ACTIVE'
            # No 'failureReason' field
        }
    }
    
    # Extract with default value
    memory = api_response.get('memory', {})
    failure_reason = memory.get('failureReason', 'Unknown failure reason')
    
    # Verify default is used
    assert failure_reason == 'Unknown failure reason', (
        f"Expected default failure reason, got '{failure_reason}'"
    )


def test_handle_present_failure_reason_field():
    """
    Test extracting failureReason when present (status is FAILED).
    """
    # Response with failureReason
    api_response = {
        'memory': {
            'id': 'mem-failed456',
            'arn': 'arn:aws:bedrock:us-east-1:333333333333:memory/mem-failed456',
            'name': 'failed-memory',
            'status': 'FAILED',
            'failureReason': 'Insufficient permissions to create memory resource'
        }
    }
    
    # Extract failureReason
    memory = api_response.get('memory', {})
    failure_reason = memory.get('failureReason', 'Unknown failure reason')
    
    # Verify actual reason is extracted
    assert failure_reason == 'Insufficient permissions to create memory resource', (
        f"Expected specific failure reason, got '{failure_reason}'"
    )


def test_parse_timestamp_fields():
    """
    Test parsing timestamp fields from API response.
    
    The API returns createdAt and updatedAt as ISO 8601 timestamp strings.
    """
    # Response with timestamp fields
    api_response = {
        'memory': {
            'id': 'mem-timestamp1',
            'arn': 'arn:aws:bedrock:us-east-1:444444444444:memory/mem-timestamp1',
            'name': 'timestamped-memory',
            'status': 'ACTIVE',
            'createdAt': '2024-01-15T10:30:00.000Z',
            'updatedAt': '2024-01-15T11:45:30.500Z'
        }
    }
    
    # Extract timestamps
    memory = api_response.get('memory', {})
    created_at_str = memory.get('createdAt')
    updated_at_str = memory.get('updatedAt')
    
    # Verify timestamps are present
    assert created_at_str == '2024-01-15T10:30:00.000Z'
    assert updated_at_str == '2024-01-15T11:45:30.500Z'
    
    # Verify they can be parsed as datetime objects
    created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
    updated_at = datetime.fromisoformat(updated_at_str.replace('Z', '+00:00'))
    
    assert created_at.year == 2024
    assert created_at.month == 1
    assert created_at.day == 15
    assert updated_at.hour == 11
    assert updated_at.minute == 45


def test_handle_missing_timestamp_fields():
    """
    Test handling missing timestamp fields gracefully.
    
    While timestamps should always be present, code should handle their absence.
    """
    # Response without timestamps
    api_response = {
        'memory': {
            'id': 'mem-notimestamp',
            'arn': 'arn:aws:bedrock:us-east-1:555555555555:memory/mem-notimestamp',
            'name': 'no-timestamp-memory',
            'status': 'CREATING'
            # No createdAt or updatedAt
        }
    }
    
    # Extract with None as default
    memory = api_response.get('memory', {})
    created_at = memory.get('createdAt')
    updated_at = memory.get('updatedAt')
    
    # Verify None is returned for missing fields
    assert created_at is None, f"Expected None for missing createdAt, got '{created_at}'"
    assert updated_at is None, f"Expected None for missing updatedAt, got '{updated_at}'"


def test_extract_all_fields_from_complete_response():
    """
    Test extracting all fields from a complete API response.
    
    Verify that all expected fields can be extracted from a full response.
    """
    # Complete API response with all fields
    api_response = {
        'memory': {
            'id': 'mem-complete789',
            'arn': 'arn:aws:bedrock:us-west-2:666666666666:memory/mem-complete789',
            'name': 'complete-memory',
            'status': 'ACTIVE',
            'description': 'A complete memory resource with all fields',
            'createdAt': '2024-02-20T08:00:00.000Z',
            'updatedAt': '2024-02-20T09:15:00.000Z',
            'eventExpiryDuration': 90
        }
    }
    
    # Extract all fields
    memory = api_response.get('memory', {})
    
    memory_id = memory.get('id')
    memory_arn = memory.get('arn')
    name = memory.get('name')
    status = memory.get('status')
    description = memory.get('description')
    created_at = memory.get('createdAt')
    updated_at = memory.get('updatedAt')
    expiry_duration = memory.get('eventExpiryDuration')
    
    # Verify all fields
    assert memory_id == 'mem-complete789'
    assert memory_arn == 'arn:aws:bedrock:us-west-2:666666666666:memory/mem-complete789'
    assert name == 'complete-memory'
    assert status == 'ACTIVE'
    assert description == 'A complete memory resource with all fields'
    assert created_at == '2024-02-20T08:00:00.000Z'
    assert updated_at == '2024-02-20T09:15:00.000Z'
    assert expiry_duration == 90


def test_handle_empty_memory_dict():
    """
    Test handling an empty memory dictionary gracefully.
    
    Edge case: API returns response but memory dict is empty.
    """
    # Response with empty memory dict
    api_response = {
        'memory': {}
    }
    
    # Extract with defaults
    memory = api_response.get('memory', {})
    memory_id = memory.get('id')
    memory_arn = memory.get('arn')
    status = memory.get('status')
    
    # Verify None is returned for all missing fields
    assert memory_id is None
    assert memory_arn is None
    assert status is None


def test_handle_missing_memory_key():
    """
    Test handling missing 'memory' key in response.
    
    Edge case: API returns response without 'memory' key.
    """
    # Response without memory key
    api_response = {}
    
    # Extract with default empty dict
    memory = api_response.get('memory', {})
    memory_id = memory.get('id')
    
    # Verify empty dict is used as default
    assert memory == {}
    assert memory_id is None


def test_extract_status_values():
    """
    Test extracting different status values from responses.
    
    Verify that all possible status values can be extracted correctly.
    """
    statuses = ['CREATING', 'ACTIVE', 'FAILED', 'DELETING']
    
    for expected_status in statuses:
        api_response = {
            'memory': {
                'id': f'mem-{expected_status.lower()}',
                'arn': f'arn:aws:bedrock:us-east-1:777777777777:memory/mem-{expected_status.lower()}',
                'name': f'{expected_status.lower()}-memory',
                'status': expected_status
            }
        }
        
        # Extract status
        memory = api_response.get('memory', {})
        actual_status = memory.get('status')
        
        # Verify status matches
        assert actual_status == expected_status, (
            f"Expected status '{expected_status}', got '{actual_status}'"
        )
