"""Unit tests for exponential backoff utility function."""

import random


def calculate_backoff_delay(
    attempt: int,
    initial_delay: float = 2.0,
    max_delay: float = 30.0,
    multiplier: float = 2.0,
    jitter_percent: float = 0.25
) -> float:
    """
    Calculate exponential backoff delay with jitter.
    
    Args:
        attempt: Current attempt number (0-indexed)
        initial_delay: Starting delay in seconds
        max_delay: Maximum delay cap in seconds
        multiplier: Exponential growth factor
        jitter_percent: Randomization factor (0.0 to 1.0)
        
    Returns:
        float: Delay in seconds for this attempt
    """
    # Calculate base delay with exponential backoff
    base_delay = initial_delay * (multiplier ** attempt)
    
    # Cap at max_delay
    base_delay = min(base_delay, max_delay)
    
    # Apply jitter: randomize by ±jitter_percent
    jitter_range = base_delay * jitter_percent
    jitter = random.uniform(-jitter_range, jitter_range)
    
    # Ensure delay is never negative
    final_delay = max(0.0, base_delay + jitter)
    
    return final_delay


def test_backoff_attempt_zero_returns_initial_delay():
    """
    Test that attempt=0 returns a value close to initial_delay.
    
    With attempt=0, the base delay should be initial_delay.
    With jitter, the result should be within ±25% of initial_delay.
    """
    initial_delay = 2.0
    jitter_percent = 0.25
    
    # Run multiple times to account for randomness
    for _ in range(10):
        delay = calculate_backoff_delay(
            attempt=0,
            initial_delay=initial_delay,
            jitter_percent=jitter_percent
        )
        
        # Verify delay is within expected bounds
        min_expected = initial_delay * (1 - jitter_percent)
        max_expected = initial_delay * (1 + jitter_percent)
        
        assert min_expected <= delay <= max_expected, (
            f"Delay {delay} not in range [{min_expected}, {max_expected}]"
        )


def test_backoff_large_attempts_cap_at_max_delay():
    """
    Test that large attempt numbers are capped at max_delay.
    
    With exponential growth, large attempts should hit the max_delay cap.
    The result should be within ±25% of max_delay due to jitter.
    """
    initial_delay = 2.0
    max_delay = 30.0
    multiplier = 2.0
    jitter_percent = 0.25
    
    # Test with very large attempt numbers
    for attempt in [10, 20, 50, 100]:
        delay = calculate_backoff_delay(
            attempt=attempt,
            initial_delay=initial_delay,
            max_delay=max_delay,
            multiplier=multiplier,
            jitter_percent=jitter_percent
        )
        
        # Verify delay is capped at max_delay (with jitter)
        min_expected = max_delay * (1 - jitter_percent)
        max_expected = max_delay * (1 + jitter_percent)
        
        assert min_expected <= delay <= max_expected, (
            f"Delay {delay} not in range [{min_expected}, {max_expected}] for attempt {attempt}"
        )


def test_backoff_jitter_stays_within_bounds():
    """
    Test that jitter keeps the delay within acceptable bounds.
    
    For any attempt, the delay should be between:
    - Minimum: base_delay * (1 - jitter_percent)
    - Maximum: base_delay * (1 + jitter_percent)
    
    But also respecting the max_delay cap.
    """
    initial_delay = 2.0
    max_delay = 30.0
    multiplier = 2.0
    jitter_percent = 0.25
    
    # Test various attempt numbers
    for attempt in range(0, 10):
        # Calculate expected base delay
        base_delay = min(initial_delay * (multiplier ** attempt), max_delay)
        
        # Run multiple times to verify jitter bounds
        for _ in range(10):
            delay = calculate_backoff_delay(
                attempt=attempt,
                initial_delay=initial_delay,
                max_delay=max_delay,
                multiplier=multiplier,
                jitter_percent=jitter_percent
            )
            
            # Verify delay is within jitter bounds
            min_expected = base_delay * (1 - jitter_percent)
            max_expected = base_delay * (1 + jitter_percent)
            
            assert min_expected <= delay <= max_expected, (
                f"Attempt {attempt}: Delay {delay} not in range [{min_expected}, {max_expected}]"
            )


def test_backoff_delay_never_negative():
    """
    Test that the delay is never negative, even with jitter.
    
    Edge case: With very small initial_delay and large jitter,
    ensure the result is always >= 0.
    """
    # Use small initial delay and large jitter
    initial_delay = 0.1
    jitter_percent = 0.9  # 90% jitter
    
    for attempt in range(0, 5):
        for _ in range(20):
            delay = calculate_backoff_delay(
                attempt=attempt,
                initial_delay=initial_delay,
                jitter_percent=jitter_percent
            )
            
            assert delay >= 0.0, f"Delay {delay} is negative for attempt {attempt}"


def test_backoff_exponential_growth():
    """
    Test that delays grow exponentially before hitting the cap.
    
    For attempts where we haven't hit max_delay, verify that
    delay roughly doubles with each attempt (within jitter tolerance).
    """
    initial_delay = 2.0
    max_delay = 100.0  # High cap to avoid hitting it
    multiplier = 2.0
    jitter_percent = 0.1  # Low jitter for clearer exponential pattern
    
    # Seed random for reproducibility in this test
    random.seed(42)
    
    delays = []
    for attempt in range(0, 5):
        delay = calculate_backoff_delay(
            attempt=attempt,
            initial_delay=initial_delay,
            max_delay=max_delay,
            multiplier=multiplier,
            jitter_percent=jitter_percent
        )
        delays.append(delay)
    
    # Verify exponential growth pattern (allowing for jitter)
    for i in range(1, len(delays)):
        # Expected ratio is 2.0, but allow for jitter
        ratio = delays[i] / delays[i-1]
        assert 1.5 < ratio < 2.5, (
            f"Ratio between attempt {i} and {i-1} is {ratio}, expected ~2.0"
        )


def test_backoff_custom_parameters():
    """
    Test that custom parameters are respected.
    
    Verify that the function works correctly with non-default parameters.
    """
    # Custom parameters
    initial_delay = 5.0
    max_delay = 50.0
    multiplier = 3.0
    jitter_percent = 0.1
    
    # Attempt 0 should be close to initial_delay
    delay_0 = calculate_backoff_delay(
        attempt=0,
        initial_delay=initial_delay,
        max_delay=max_delay,
        multiplier=multiplier,
        jitter_percent=jitter_percent
    )
    
    assert 4.5 <= delay_0 <= 5.5, f"Delay {delay_0} not close to initial_delay {initial_delay}"
    
    # Attempt 1 should be close to initial_delay * multiplier
    delay_1 = calculate_backoff_delay(
        attempt=1,
        initial_delay=initial_delay,
        max_delay=max_delay,
        multiplier=multiplier,
        jitter_percent=jitter_percent
    )
    
    expected_1 = initial_delay * multiplier
    assert expected_1 * 0.9 <= delay_1 <= expected_1 * 1.1, (
        f"Delay {delay_1} not close to expected {expected_1}"
    )
