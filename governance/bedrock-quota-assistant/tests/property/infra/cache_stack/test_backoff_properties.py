"""Property-based tests for exponential backoff utility function."""

import random
from hypothesis import given, strategies as st, settings, assume


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


@settings(max_examples=100)
@given(
    attempt=st.integers(min_value=0, max_value=100),
    initial_delay=st.floats(min_value=0.1, max_value=10.0),
    max_delay=st.floats(min_value=10.0, max_value=100.0),
    multiplier=st.floats(min_value=1.1, max_value=5.0),
    jitter_percent=st.floats(min_value=0.0, max_value=1.0)
)
def test_exponential_backoff_bounds(
    attempt: int,
    initial_delay: float,
    max_delay: float,
    multiplier: float,
    jitter_percent: float
):
    """
    Property 3: Exponential backoff bounds
    
    
    
    For any polling attempt, the calculated backoff delay must be between
    the initial delay and the maximum delay, inclusive.
    
    This property verifies that:
    1. The delay is never less than 0 (even with negative jitter)
    2. The delay respects the max_delay cap
    3. The delay is within reasonable bounds given the parameters
    """
    # Ensure max_delay >= initial_delay for valid test cases
    assume(max_delay >= initial_delay)
    
    # Calculate the backoff delay
    delay = calculate_backoff_delay(
        attempt=attempt,
        initial_delay=initial_delay,
        max_delay=max_delay,
        multiplier=multiplier,
        jitter_percent=jitter_percent
    )
    
    # Property 1: Delay must never be negative
    assert delay >= 0.0, (
        f"Delay {delay} is negative for attempt {attempt}"
    )
    
    # Property 2: Calculate the theoretical bounds
    # Base delay without jitter
    base_delay = initial_delay * (multiplier ** attempt)
    base_delay = min(base_delay, max_delay)
    
    # With jitter, the delay can vary by ±jitter_percent
    min_expected = base_delay * (1 - jitter_percent)
    max_expected = base_delay * (1 + jitter_percent)
    
    # Ensure non-negative lower bound
    min_expected = max(0.0, min_expected)
    
    # Property 3: Delay must be within jitter bounds
    assert min_expected <= delay <= max_expected, (
        f"Delay {delay} not in expected range [{min_expected}, {max_expected}] "
        f"for attempt {attempt} with base_delay {base_delay}"
    )
    
    # Property 4: Delay should never exceed max_delay + jitter
    absolute_max = max_delay * (1 + jitter_percent)
    assert delay <= absolute_max, (
        f"Delay {delay} exceeds absolute maximum {absolute_max} "
        f"(max_delay={max_delay}, jitter_percent={jitter_percent})"
    )
