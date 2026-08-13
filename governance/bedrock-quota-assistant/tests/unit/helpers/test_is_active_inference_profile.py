"""
Unit tests for ``helpers.profile_resolution.is_active_inference_profile``.

This is the single source of truth for the per-profile-metrics asymmetric
inclusion rule (see ``.kiro/specs/per-profile-metrics/design.md`` §"Component
6" and requirements.md 3.2, 3.3, 3.5):

- ``application_profile`` → always active (the customer had to deliberately
  create the Application_Profile, so its existence is signal regardless of
  recent traffic volume). The ``show_all_system_profiles`` override flag
  does not apply to this branch.
- ``system_profile``:
  - ``show_all_system_profiles=True`` → always active (explicit opt-in
    override used when the user asks to see "all", "inactive", "every", or
    "available" profiles).
  - ``show_all_system_profiles=False`` → active iff ``invocations_in_window
    >= MIN_INVOCATIONS_FOR_ACTIVE`` (threshold pinned to 5).

The test is fully table-parametrized across the Cartesian product of:
- invocation counts: ``{0, 4, 5, 1_000_000}`` (below, just-below, at-threshold,
  far-above)
- profile kinds: ``{"system_profile", "application_profile"}``
- override flag: ``{True, False}``

→ 4 × 2 × 2 = 16 cases, one assert per case.
"""

import pytest

from helpers.profile_resolution import (
    is_active_inference_profile,
    MIN_INVOCATIONS_FOR_ACTIVE,
)


# ---------------------------------------------------------------------------
# Parametrized table — all 16 combinations of:
#   invocations × profile_kind × show_all_system_profiles
# ---------------------------------------------------------------------------
CASES = [
    # --- system_profile, show_all_system_profiles=False (threshold applies) ---
    ("sys_inv0_flagF",         "system_profile",      0,          False, False),
    ("sys_inv4_flagF",         "system_profile",      4,          False, False),
    ("sys_inv5_flagF",         "system_profile",      5,          False, True),
    ("sys_invMax_flagF",       "system_profile",      1_000_000,  False, True),
    # --- system_profile, show_all_system_profiles=True (override → always True) ---
    ("sys_inv0_flagT",         "system_profile",      0,          True,  True),
    ("sys_inv4_flagT",         "system_profile",      4,          True,  True),
    ("sys_inv5_flagT",         "system_profile",      5,          True,  True),
    ("sys_invMax_flagT",       "system_profile",      1_000_000,  True,  True),
    # --- application_profile, show_all_system_profiles=False (flag irrelevant → always True) ---
    ("app_inv0_flagF",         "application_profile", 0,          False, True),
    ("app_inv4_flagF",         "application_profile", 4,          False, True),
    ("app_inv5_flagF",         "application_profile", 5,          False, True),
    ("app_invMax_flagF",       "application_profile", 1_000_000,  False, True),
    # --- application_profile, show_all_system_profiles=True (flag irrelevant → always True) ---
    ("app_inv0_flagT",         "application_profile", 0,          True,  True),
    ("app_inv4_flagT",         "application_profile", 4,          True,  True),
    ("app_inv5_flagT",         "application_profile", 5,          True,  True),
    ("app_invMax_flagT",       "application_profile", 1_000_000,  True,  True),
]


@pytest.mark.parametrize(
    "profile_kind, invocations_in_window, show_all_system_profiles, expected",
    [(kind, inv, flag, exp) for (_id, kind, inv, flag, exp) in CASES],
    ids=[case[0] for case in CASES],
)
def test_is_active_inference_profile_table(
    profile_kind,
    invocations_in_window,
    show_all_system_profiles,
    expected,
):
    """One assertion per (kind, invocations, flag) triple."""
    result = is_active_inference_profile(
        profile_kind,
        invocations_in_window,
        show_all_system_profiles=show_all_system_profiles,
    )
    assert result is expected, (
        f"is_active_inference_profile("
        f"profile_kind={profile_kind!r}, "
        f"invocations_in_window={invocations_in_window}, "
        f"show_all_system_profiles={show_all_system_profiles}"
        f") returned {result!r}, expected {expected!r}"
    )


def test_threshold_constant_is_five():
    """Pin ``MIN_INVOCATIONS_FOR_ACTIVE`` to the documented value."""
    assert MIN_INVOCATIONS_FOR_ACTIVE == 5


def test_show_all_defaults_to_false():
    """The ``show_all_system_profiles`` parameter defaults to ``False``."""
    assert is_active_inference_profile("system_profile", 0) is False
    assert is_active_inference_profile("system_profile", 5) is True
