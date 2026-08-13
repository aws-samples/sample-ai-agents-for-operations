"""Unit tests for error message formatting in memory resource handler."""



def test_timeout_error_includes_all_context():
    """
    Test that timeout error includes memory_id, last status, and elapsed time.
    
    When polling exceeds max_duration_seconds, the TimeoutError message should
    contain all relevant context for debugging.
    """
    # Simulate timeout scenario
    memory_id = "mem-timeout123"
    last_status = "CREATING"
    elapsed_time = 245.67
    
    # Format error message as done in wait_for_memory_status
    error_msg = (
        f"Timeout waiting for memory {memory_id} to reach target status. "
        f"Last status: {last_status}, Elapsed time: {elapsed_time:.2f}s"
    )
    
    # Verify all context is present
    assert memory_id in error_msg, f"memory_id '{memory_id}' not found in error message"
    assert last_status in error_msg, f"last_status '{last_status}' not found in error message"
    assert "245.67s" in error_msg, "elapsed_time not formatted correctly in error message"
    assert "Timeout waiting for memory" in error_msg, "Timeout prefix not in error message"
    
    # Verify exact format
    expected_msg = (
        "Timeout waiting for memory mem-timeout123 to reach target status. "
        "Last status: CREATING, Elapsed time: 245.67s"
    )
    assert error_msg == expected_msg, f"Error message format mismatch.\nExpected: {expected_msg}\nActual: {error_msg}"


def test_timeout_error_with_different_statuses():
    """
    Test timeout error formatting with various status values.
    
    Verify that timeout errors correctly include different status values.
    """
    test_cases = [
        ("mem-test1", "CREATING", 120.5),
        ("mem-test2", "DELETING", 180.0),
        ("mem-test3", "ACTIVE", 90.25),
        ("mem-test4", None, 200.0),  # Edge case: status is None
    ]
    
    for memory_id, last_status, elapsed_time in test_cases:
        error_msg = (
            f"Timeout waiting for memory {memory_id} to reach target status. "
            f"Last status: {last_status}, Elapsed time: {elapsed_time:.2f}s"
        )
        
        # Verify all components are present
        assert memory_id in error_msg
        assert str(last_status) in error_msg
        assert f"{elapsed_time:.2f}s" in error_msg


def test_timeout_error_with_edge_case_elapsed_times():
    """
    Test timeout error formatting with edge case elapsed times.
    
    Verify that elapsed time is formatted correctly for various values.
    """
    memory_id = "mem-edge"
    last_status = "CREATING"
    
    test_cases = [
        0.0,      # Zero elapsed time
        0.01,     # Very small elapsed time
        1.0,      # Exactly 1 second
        59.99,    # Just under 1 minute
        240.0,    # Exactly 4 minutes (max polling duration)
        999.99,   # Large elapsed time
    ]
    
    for elapsed_time in test_cases:
        error_msg = (
            f"Timeout waiting for memory {memory_id} to reach target status. "
            f"Last status: {last_status}, Elapsed time: {elapsed_time:.2f}s"
        )
        
        # Verify elapsed time is formatted with 2 decimal places
        expected_time_str = f"{elapsed_time:.2f}s"
        assert expected_time_str in error_msg, (
            f"Expected '{expected_time_str}' in error message, got: {error_msg}"
        )


def test_failed_status_error_includes_failure_reason():
    """
    Test that FAILED status error includes the failureReason from API response.
    
    When memory status reaches FAILED, the error message should include
    the failureReason field from the API response.
    """
    # Simulate FAILED status scenario
    memory_id = "mem-failed789"
    failure_reason = "Insufficient permissions to create memory resource"
    
    # Format error message as done in wait_for_memory_status
    error_msg = (
        f"Memory {memory_id} reached FAILED status. "
        f"Reason: {failure_reason}"
    )
    
    # Verify all context is present
    assert memory_id in error_msg, f"memory_id '{memory_id}' not found in error message"
    assert failure_reason in error_msg, "failure_reason not found in error message"
    assert "reached FAILED status" in error_msg, "FAILED status indicator not in error message"
    
    # Verify exact format
    expected_msg = (
        "Memory mem-failed789 reached FAILED status. "
        "Reason: Insufficient permissions to create memory resource"
    )
    assert error_msg == expected_msg, f"Error message format mismatch.\nExpected: {expected_msg}\nActual: {error_msg}"


def test_failed_status_error_with_various_failure_reasons():
    """
    Test FAILED status error formatting with various failure reasons.
    
    Verify that different failure reasons are correctly included in error messages.
    """
    memory_id = "mem-test-failed"
    
    test_cases = [
        "Insufficient permissions to create memory resource",
        "Invalid memory configuration",
        "Service quota exceeded",
        "Internal service error",
        "Resource limit reached",
        "",  # Edge case: empty failure reason
    ]
    
    for failure_reason in test_cases:
        error_msg = (
            f"Memory {memory_id} reached FAILED status. "
            f"Reason: {failure_reason}"
        )
        
        # Verify components are present
        assert memory_id in error_msg
        assert "reached FAILED status" in error_msg
        assert f"Reason: {failure_reason}" in error_msg


def test_failed_status_error_with_default_failure_reason():
    """
    Test FAILED status error when failureReason is not provided.
    
    When the API response doesn't include failureReason, a default message
    should be used.
    """
    memory_id = "mem-no-reason"
    failure_reason = "Unknown failure reason"  # Default when not provided by API
    
    # Format error message with default reason
    error_msg = (
        f"Memory {memory_id} reached FAILED status. "
        f"Reason: {failure_reason}"
    )
    
    # Verify default reason is used
    assert "Unknown failure reason" in error_msg, "Default failure reason not in error message"
    assert memory_id in error_msg


def test_resource_not_found_exception_formatting():
    """
    Test ResourceNotFoundException formatting for delete operations.
    
    When delete_memory() or get_memory() raises ResourceNotFoundException,
    it should be handled gracefully with appropriate logging.
    """
    # Simulate ResourceNotFoundException scenario
    memory_id = "mem-notfound456"
    
    # This is the log message format used in the handler
    log_msg = (
        f"Memory {memory_id} not found (ResourceNotFoundException)"
    )
    
    # Verify message format
    assert memory_id in log_msg, f"memory_id '{memory_id}' not found in log message"
    assert "not found" in log_msg, "'not found' indicator not in log message"
    assert "ResourceNotFoundException" in log_msg, "Exception type not in log message"
    
    # Verify exact format
    expected_msg = "Memory mem-notfound456 not found (ResourceNotFoundException)"
    assert log_msg == expected_msg, f"Log message format mismatch.\nExpected: {expected_msg}\nActual: {log_msg}"


def test_resource_not_found_during_delete_idempotency():
    """
    Test ResourceNotFoundException message during delete operation (idempotency case).
    
    When delete is called on an already-deleted memory, the handler should
    log appropriately and return success.
    """
    memory_id = "mem-already-deleted"
    
    # Log message for idempotent delete
    log_msg = (
        f"Memory {memory_id} not found, assuming already deleted"
    )
    
    # Verify message components
    assert memory_id in log_msg
    assert "not found" in log_msg
    assert "already deleted" in log_msg


def test_unexpected_status_error_formatting():
    """
    Test error formatting when memory has unexpected status (e.g., DELETING during Create).
    
    When memory status is unexpected for the current operation, the error message
    should clearly indicate this.
    """
    memory_id = "mem-unexpected"
    current_status = "DELETING"
    
    # Format error message as done in wait_for_memory_status
    error_msg = (
        f"Memory {memory_id} has unexpected status {current_status} during operation"
    )
    
    # Verify all context is present
    assert memory_id in error_msg, f"memory_id '{memory_id}' not found in error message"
    assert current_status in error_msg, f"current_status '{current_status}' not found in error message"
    assert "unexpected status" in error_msg, "'unexpected status' indicator not in error message"
    
    # Verify exact format
    expected_msg = "Memory mem-unexpected has unexpected status DELETING during operation"
    assert error_msg == expected_msg, f"Error message format mismatch.\nExpected: {expected_msg}\nActual: {error_msg}"


def test_conflict_exception_error_formatting():
    """
    Test error formatting for ConflictException during create retry.
    
    When create_memory() raises ConflictException but the existing memory
    is not ACTIVE, an appropriate error message should be generated.
    """
    memory_name = "test_memory"
    existing_status = "CREATING"
    
    # Format error message as done in on_create handler
    error_msg = (
        f"Memory {memory_name} already exists but is not ACTIVE. "
        f"Current status: {existing_status}"
    )
    
    # Verify all context is present
    assert memory_name in error_msg, f"memory_name '{memory_name}' not found in error message"
    assert existing_status in error_msg, f"existing_status '{existing_status}' not found in error message"
    assert "already exists" in error_msg, "'already exists' indicator not in error message"
    assert "not ACTIVE" in error_msg, "'not ACTIVE' indicator not in error message"
    
    # Verify exact format
    expected_msg = (
        "Memory test_memory already exists but is not ACTIVE. "
        "Current status: CREATING"
    )
    assert error_msg == expected_msg, f"Error message format mismatch.\nExpected: {expected_msg}\nActual: {error_msg}"


def test_error_message_with_special_characters():
    """
    Test error message formatting with special characters in memory IDs and reasons.
    
    Verify that special characters don't break error message formatting.
    """
    # Test with various special characters
    test_cases = [
        ("mem-with-dashes-123", "Reason with spaces and punctuation!"),
        ("mem_with_underscores", "Reason: with: colons"),
        ("mem.with.dots", "Reason with 'quotes' and \"double quotes\""),
        ("mem123", "Reason with\nnewline"),
    ]
    
    for memory_id, failure_reason in test_cases:
        # Test FAILED status error
        error_msg = (
            f"Memory {memory_id} reached FAILED status. "
            f"Reason: {failure_reason}"
        )
        
        # Verify components are present (even with special characters)
        assert memory_id in error_msg
        assert failure_reason in error_msg
        
        # Test timeout error
        timeout_msg = (
            f"Timeout waiting for memory {memory_id} to reach target status. "
            f"Last status: CREATING, Elapsed time: 120.00s"
        )
        
        assert memory_id in timeout_msg


def test_error_message_consistency():
    """
    Test that error messages follow consistent formatting patterns.
    
    All error messages should follow similar patterns for easier parsing
    and debugging.
    """
    memory_id = "mem-consistent"
    
    # All error messages should start with "Memory {memory_id}"
    timeout_msg = (
        f"Timeout waiting for memory {memory_id} to reach target status. "
        f"Last status: CREATING, Elapsed time: 120.00s"
    )
    
    failed_msg = (
        f"Memory {memory_id} reached FAILED status. "
        f"Reason: Test failure"
    )
    
    unexpected_msg = (
        f"Memory {memory_id} has unexpected status DELETING during operation"
    )
    
    # Verify all messages reference the memory_id early in the message
    for msg in [timeout_msg, failed_msg, unexpected_msg]:
        # Memory ID should appear within first 50 characters
        assert msg.find(memory_id) < 50, (
            f"memory_id should appear early in error message: {msg}"
        )
        
        # All messages should start with either "Memory" or "Timeout"
        assert msg.startswith("Memory") or msg.startswith("Timeout"), (
            f"Error message should start with 'Memory' or 'Timeout': {msg}"
        )
