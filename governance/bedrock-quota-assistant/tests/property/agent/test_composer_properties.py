"""
Hypothesis property tests for ``compose_profile_grain_output``.

The composer is the single source of truth for Templates B and C from
``.kiro/specs/per-profile-metrics/design.md``. It must guarantee these
universal properties for any list of ``ProfileUsageRow`` entries:

1. Section headers appear in the fixed order (Req 2.1):

   - ``on-demand`` first (if any)
   - ``cross-region-geo`` sections alphabetical by geography code
     (``ap``, ``eu``, ``jp``, ``us``)
   - ``cross-region-global``
   - ``application-profile`` sections alphabetical by profile name
     (case-insensitive)

2. A ``Totals (across profiles above)`` row appears iff two or more
   sections are emitted (Req 2.3 and 2.4).

3. Every row's identifying label (CW_Model_ID for non-app rows,
   ``application_profile_name`` for app-profile rows) is rendered in the
   output (Req 2.2).

Each property is expressed as a separate Hypothesis test so a counter-example
points unambiguously at the branch it came from.
"""

import warnings

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st


# Suppress any stray Hypothesis warnings so the pytest output stays focused
# on pass/fail signal.
warnings.filterwarnings("ignore", category=UserWarning, module=r"hypothesis.*")

from helpers.response_composer import (
    compose_profile_grain_output,
    _PROFILE_SECTION_ORDER,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# All access-pattern labels the composer recognises. Taken directly from
# ``ProfileUsageRow.access_pattern_label`` in ``src/agent.py`` so if a new
# label is introduced the type checker flags the mismatch here first.
_ACCESS_PATTERN_LABELS = [
    "on-demand",
    "cross-region-geo (ap)",
    "cross-region-geo (eu)",
    "cross-region-geo (jp)",
    "cross-region-geo (us)",
    "cross-region-global",
    "application-profile",
]

# ASCII-friendly identifier strategy: letters, digits, hyphen, underscore.
# Matches the shape of real CW_Model_IDs and Application_Profile names.
# Restricting the character set keeps counter-examples readable and avoids
# accidental collisions with whitespace or formatting characters in the
# rendered output.
_IDENTIFIER = st.text(
    min_size=1,
    max_size=20,
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="-_",
    ),
)


@st.composite
def profile_usage_row_strategy(draw) -> dict:
    """Build a single ``ProfileUsageRow``-shaped dict.

    Application_Profile rows carry a non-None name and ARN. Other rows carry
    ``None`` for both name and ARN, matching the TypedDict contract in
    ``src/agent.py``. The label-conditional branch lives inside the strategy
    so every generated row is self-consistent.
    """
    label = draw(st.sampled_from(_ACCESS_PATTERN_LABELS))
    cw_model_id = draw(_IDENTIFIER)

    if label == "application-profile":
        app_name = draw(_IDENTIFIER)
        app_arn = (
            "arn:aws:bedrock:us-east-1:123456789012:"
            f"application-inference-profile/{draw(_IDENTIFIER)}"
        )
    else:
        app_name = None
        app_arn = None

    return {
        "cw_model_id": cw_model_id,
        "access_pattern_label": label,
        "application_profile_name": app_name,
        "application_profile_arn": app_arn,
        "invocations_in_window": draw(
            st.integers(min_value=0, max_value=10_000_000)
        ),
        "peak_rpm": draw(st.floats(min_value=0.0, max_value=10_000.0)),
        "peak_input_tpm": draw(st.floats(min_value=0.0, max_value=1_000_000.0)),
        "peak_output_tpm": draw(
            st.floats(min_value=0.0, max_value=1_000_000.0)
        ),
        "no_recent_usage_label": draw(st.booleans()),
        "inactive_available_label": draw(st.booleans()),
    }


def _section_marker(row: dict) -> str:
    """Return the composer's section-header marker for a row.

    Mirrors the header line the composer emits:
      ``[ {label} ]  {display}``

    where ``display`` is the Application_Profile name for app-profile rows
    and the CW_Model_ID for every other row. The marker intentionally omits
    the ``(no recent usage)`` / ``(inactive — …)`` suffix so dedup/search
    is stable across rows that carry suffixes.
    """
    label = row["access_pattern_label"]
    if label == "application-profile":
        display = row.get("application_profile_name") or row["cw_model_id"]
    else:
        display = row["cw_model_id"]
    return f"[ {label} ]  {display}"


def _sort_key(row: dict) -> tuple[int, str]:
    """The key the composer uses to order sections.

    Mirrors ``compose_profile_grain_output._sort_key``:
    ``(_PROFILE_SECTION_ORDER[label], app_name.lower() or "")``.
    """
    label = row["access_pattern_label"]
    primary = _PROFILE_SECTION_ORDER[label]
    if label == "application-profile":
        name = row.get("application_profile_name") or ""
        return (primary, name.lower())
    return (primary, "")


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

# Shared settings: profile-grain composer is pure and fast, so 100 examples
# is plenty. ``HealthCheck.too_slow`` is suppressed because the composite
# row strategy does a handful of ``draw`` calls which Hypothesis can
# occasionally flag on slow CI runners.
_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@pytest.mark.property_test
@_SETTINGS
@given(rows=st.lists(profile_usage_row_strategy(), max_size=15))
def test_section_ordering_matches_fixed_order(rows):
    """Section headers appear in the fixed priority order.

    Validates: Requirements 2.1, 2.2.

    For every list of rows, the composed output must emit section headers
    in the order defined by ``_PROFILE_SECTION_ORDER`` (on-demand first,
    then cross-region-geo sections in alphabetical-by-geography order, then
    cross-region-global, then application-profile rows alphabetical by
    name). The check finds each section marker's position in the output
    string and asserts the positions are non-decreasing when walked in the
    composer's expected sort order.
    """
    out = compose_profile_grain_output("Model", "us-east-1", 24, rows)

    if len(rows) < 2:
        # With 0 or 1 rows there is nothing to order; the composer's header
        # and (single) section are trivially in order.
        return

    sorted_rows = sorted(rows, key=_sort_key)

    # Deduplicate by section marker: two rows with identical labels AND
    # identical displays (e.g. two ``on-demand`` rows that happen to share a
    # CW_Model_ID, or two ``application-profile`` rows with the same name)
    # produce the same marker string. ``str.find`` returns the first
    # occurrence, so keeping only the first instance preserves the ordering
    # semantics without flagging those duplicate-marker cases as ordering
    # violations.
    seen: set[str] = set()
    ordered_markers: list[str] = []
    for row in sorted_rows:
        m = _section_marker(row)
        if m not in seen:
            seen.add(m)
            ordered_markers.append(m)

    def _find_marker(text, marker):
        """Find marker position, disambiguating from longer markers that share a prefix."""
        start = 0
        while True:
            pos = text.find(marker, start)
            if pos < 0:
                return -1
            # Check that the character after the marker is not alphanumeric
            # (i.e., the marker isn't a prefix of a longer model ID)
            end = pos + len(marker)
            if end >= len(text) or not text[end].isalnum():
                return pos
            start = end

    indices = [_find_marker(out, m) for m in ordered_markers]
    for m, idx in zip(ordered_markers, indices):
        assert idx >= 0, (
            f"Section marker {m!r} not found in composed output. "
            f"Output was:\n{out}"
        )

    assert indices == sorted(indices), (
        "Section markers appear out of order.\n"
        f"Expected order (by composer sort key): {ordered_markers}\n"
        f"Positions found in output:             {indices}\n"
        f"Output was:\n{out}"
    )


@pytest.mark.property_test
@_SETTINGS
@given(rows=st.lists(profile_usage_row_strategy(), max_size=15))
def test_totals_row_appears_iff_multi_section(rows):
    """Totals row appears iff two or more sections are emitted.

    Validates: Requirements 2.3, 2.4.

    - ``len(rows) >= 2`` → the composer must emit a Totals block labelled
      ``"Totals (across profiles above)"`` (Req 2.3).
    - ``len(rows) <= 1`` → the composer must suppress the Totals block
      entirely (Req 2.4).
    """
    out = compose_profile_grain_output("Model", "us-east-1", 24, rows)
    has_totals = "Totals (across profiles above)" in out

    if len(rows) >= 2:
        assert has_totals, (
            f"Totals row must appear when 2+ sections are emitted "
            f"(got {len(rows)} rows).\nOutput was:\n{out}"
        )
    else:
        assert not has_totals, (
            f"Totals row must NOT appear for fewer than 2 sections "
            f"(got {len(rows)} rows).\nOutput was:\n{out}"
        )


@pytest.mark.property_test
@_SETTINGS
@given(rows=st.lists(profile_usage_row_strategy(), min_size=1, max_size=15))
def test_every_row_is_represented_in_output(rows):
    """Every row contributes an identifier to the rendered output.

    Validates: Requirement 2.2.

    For Application_Profile rows the friendly name must appear in the
    output. For every other row the CW_Model_ID must appear. This is the
    minimal "no row gets silently dropped" guarantee that backs up the
    ordering property above.
    """
    out = compose_profile_grain_output("Model", "us-east-1", 24, rows)

    for row in rows:
        if row["access_pattern_label"] == "application-profile":
            name = row["application_profile_name"]
            assert name is not None and name in out, (
                f"Application_Profile name {name!r} missing from output.\n"
                f"Row: {row}\nOutput was:\n{out}"
            )
        else:
            cw_model_id = row["cw_model_id"]
            assert cw_model_id in out, (
                f"CW_Model_ID {cw_model_id!r} missing from output.\n"
                f"Row: {row}\nOutput was:\n{out}"
            )
