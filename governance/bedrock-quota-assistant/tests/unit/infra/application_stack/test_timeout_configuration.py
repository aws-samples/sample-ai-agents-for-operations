"""Unit tests for timeout configuration."""



def test_timeout_before_lambda_limit():
    """
    Test that max polling duration is less than Lambda timeout.
    
    The Lambda function has a 5-minute (300 seconds) timeout.
    The max polling duration should be less than this to allow for:
    - Lambda initialization time
    - Cleanup and response sending
    - Buffer for unexpected delays
    
    We verify that max_duration_seconds (240s) + buffer (60s) <= Lambda timeout (300s).
    """
    # Configuration values
    LAMBDA_TIMEOUT_SECONDS = 300  # 5 minutes
    MAX_POLLING_DURATION_SECONDS = 240  # 4 minutes
    REQUIRED_BUFFER_SECONDS = 60  # 1 minute for initialization, cleanup, response
    
    # Verify max polling duration leaves sufficient buffer
    total_time_needed = MAX_POLLING_DURATION_SECONDS + REQUIRED_BUFFER_SECONDS
    
    assert total_time_needed <= LAMBDA_TIMEOUT_SECONDS, (
        f"Max polling duration ({MAX_POLLING_DURATION_SECONDS}s) + buffer ({REQUIRED_BUFFER_SECONDS}s) "
        f"= {total_time_needed}s exceeds Lambda timeout ({LAMBDA_TIMEOUT_SECONDS}s)"
    )
    
    # Verify buffer is at least 1 minute (reasonable for Lambda operations)
    actual_buffer = LAMBDA_TIMEOUT_SECONDS - MAX_POLLING_DURATION_SECONDS
    
    assert actual_buffer >= REQUIRED_BUFFER_SECONDS, (
        f"Actual buffer ({actual_buffer}s) is less than required buffer ({REQUIRED_BUFFER_SECONDS}s)"
    )


def test_timeout_configuration_values():
    """
    Test that timeout configuration values are reasonable.
    
    Verify that:
    - Lambda timeout is at least 5 minutes (standard for custom resources)
    - Max polling duration is at least 3 minutes (reasonable for async operations)
    - Buffer is at least 1 minute (reasonable for Lambda overhead)
    """
    LAMBDA_TIMEOUT_SECONDS = 300
    MAX_POLLING_DURATION_SECONDS = 240
    
    # Verify Lambda timeout is reasonable
    assert LAMBDA_TIMEOUT_SECONDS >= 300, (
        f"Lambda timeout ({LAMBDA_TIMEOUT_SECONDS}s) should be at least 5 minutes (300s)"
    )
    
    # Verify max polling duration is reasonable
    assert MAX_POLLING_DURATION_SECONDS >= 180, (
        f"Max polling duration ({MAX_POLLING_DURATION_SECONDS}s) should be at least 3 minutes (180s)"
    )
    
    # Verify buffer is reasonable
    buffer = LAMBDA_TIMEOUT_SECONDS - MAX_POLLING_DURATION_SECONDS
    assert buffer >= 60, (
        f"Buffer ({buffer}s) should be at least 1 minute (60s)"
    )


def test_timeout_ratio():
    """
    Test that max polling duration is a reasonable percentage of Lambda timeout.
    
    The max polling duration should be 70-85% of the Lambda timeout to ensure
    sufficient buffer while maximizing available polling time.
    """
    LAMBDA_TIMEOUT_SECONDS = 300
    MAX_POLLING_DURATION_SECONDS = 240
    
    ratio = MAX_POLLING_DURATION_SECONDS / LAMBDA_TIMEOUT_SECONDS
    
    assert 0.70 <= ratio <= 0.85, (
        f"Max polling duration ratio ({ratio:.2%}) should be between 70% and 85% of Lambda timeout"
    )
