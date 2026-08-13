"""
Hypothesis property tests for ``is_active_inference_profile``.

The asymmetric inclusion rule has two universal properties that must hold for
every non-negative invocation count and every value of the override flag:

1. An Application_Profile is always classified active, regardless of the
   invocation count or the ``show_all_system_profiles`` flag. The customer
   had to deliberately create the profile, so its existence is signal.

2. A System_Defined_Profile is classified active if and only if the
   invocation count in the Lookback_Window meets the
   ``MIN_INVOCATIONS_FOR_ACTIVE`` threshold, OR the
   ``show_all_system_profiles`` override flag is set.

These are expressed as two separate Hypothesis tests — one property per test —
so a failure points unambiguously at the branch it came from.
"""

import warnings

import pytest
from hypothesis import given, strategies as st

from helpers.profile_resolution import (
    is_active_inference_profile,
    MIN_INVOCATIONS_FOR_ACTIVE,
)


# Suppress any stray Hypothesis-emitted noise so the pytest output stays
# focused on pass/fail signal.
warnings.filterwarnings("ignore", category=UserWarning, module=r"hypothesis.*")


# ``max_value`` is picked to be large enough that any realistic invocation
# count falls inside the generator (10M invocations in a 24h window would be
# an extreme customer) while staying well below Hypothesis' default upper
# bound so counter-example shrinking finishes quickly.
_INVOCATIONS = st.integers(min_value=0, max_value=10_000_000)
_FLAG = st.booleans()


@pytest.mark.property_test
@given(invocations=_INVOCATIONS, show_all=_FLAG)
def test_application_profile_always_active(invocations, show_all):
    """Application_Profiles are always classified active.

    For every non-negative invocation count and every value of the
    ``show_all_system_profiles`` flag, calling
    ``is_active_inference_profile`` with ``profile_kind="application_profile"``
    must return ``True``. The flag is irrelevant for this branch.
    """
    assert (
        is_active_inference_profile(
            "application_profile",
            invocations,
            show_all_system_profiles=show_all,
        )
        is True
    )


@pytest.mark.property_test
@given(invocations=_INVOCATIONS, show_all=_FLAG)
def test_system_profile_active_iff_threshold_or_override(invocations, show_all):
    """System_Defined_Profiles follow the asymmetric threshold rule.

    For every non-negative invocation count and every value of the
    ``show_all_system_profiles`` flag, calling
    ``is_active_inference_profile`` with ``profile_kind="system_profile"``
    must return ``True`` iff the flag is set OR the invocation count meets
    the ``MIN_INVOCATIONS_FOR_ACTIVE`` threshold.
    """
    expected = show_all or invocations >= MIN_INVOCATIONS_FOR_ACTIVE
    actual = is_active_inference_profile(
        "system_profile",
        invocations,
        show_all_system_profiles=show_all,
    )
    assert actual is expected, (
        f"is_active_inference_profile('system_profile', {invocations}, "
        f"show_all_system_profiles={show_all}) returned {actual!r}, "
        f"expected {expected!r} (threshold="
        f"{MIN_INVOCATIONS_FOR_ACTIVE})"
    )
