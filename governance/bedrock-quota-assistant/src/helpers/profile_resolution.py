# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Profile reference resolution for Bedrock Quota Agent."""

import re
from typing import Literal, TypedDict

from models import resolve_model_id


class ResolvedProfileRef(TypedDict):
    """Result of resolving a user-supplied Profile_Ref to a concrete target."""
    kind: Literal[
        "model",
        "system_profile",
        "application_profile",
        "ambiguous",
        "unresolved",
    ]
    cw_model_id: str | None
    base_model_id: str | None
    application_profile_name: str | None
    application_profile_arn: str | None
    underlying_quota_scope: Literal["on-demand", "cross-region"] | None
    candidates: list[str]
    unresolved_ref: str | None


class ProfileUsageRow(TypedDict):
    """Composable row used by list_active_inference_profiles and by the
    by_profile=True branch of list_active_bedrock_models."""
    cw_model_id: str
    access_pattern_label: Literal[
        "on-demand",
        "cross-region-geo (us)",
        "cross-region-geo (eu)",
        "cross-region-geo (ap)",
        "cross-region-geo (jp)",
        "cross-region-global",
        "application-profile",
    ]
    application_profile_name: str | None
    application_profile_arn: str | None
    invocations_in_window: int
    peak_rpm: float
    peak_input_tpm: float
    peak_output_tpm: float
    no_recent_usage_label: bool
    inactive_available_label: bool


MIN_INVOCATIONS_FOR_ACTIVE = 5

GEO_PREFIXES = ("us.", "eu.", "ap.", "jp.", "global.")

_PROFILE_FAMILY_KEYWORDS = {"us", "eu", "ap", "jp", "global"}

_APP_PROFILE_ARN_RE = re.compile(
    r"^arn:aws:bedrock:[^:]+:[^:]+:application-inference-profile/.+$"
)

# Pattern matching valid Bedrock model IDs: provider.model-name[-version]
# Must have exactly one dot separating provider from model name (no geo prefix dots).
_BEDROCK_MODEL_ID_RE = re.compile(
    r"^[a-z][a-z0-9]*\.[a-z][a-z0-9\-]+$"
)


def is_active_inference_profile(
    profile_kind: Literal["system_profile", "application_profile"],
    invocations_in_window: int,
    show_all_system_profiles: bool = False,
) -> bool:
    """The single source of truth for the asymmetric inclusion rule."""
    if profile_kind == "application_profile":
        return True
    if show_all_system_profiles:
        return True
    return invocations_in_window >= MIN_INVOCATIONS_FOR_ACTIVE


def _iter_snapshot_app_profiles(snapshot: dict | None):
    """Yield (app_profile_dict, base_model_id) for every Application_Profile in snapshot."""
    if not snapshot:
        return
    for model_entry in snapshot.get("models") or []:
        base_model_id = model_entry.get("model_id")
        for app_profile in model_entry.get("app_profiles") or []:
            yield app_profile, base_model_id


def _scope_from_wraps(wraps_value) -> Literal["on-demand", "cross-region"] | None:
    """Translate an AppProfile wraps field into an underlying_quota_scope."""
    if wraps_value == "cross-region":
        return "cross-region"
    if wraps_value == "on-demand":
        return "on-demand"
    return None


def _empty_resolved_profile_ref() -> ResolvedProfileRef:
    """Return a ResolvedProfileRef with every field set to its neutral value."""
    return {
        "kind": "unresolved",
        "cw_model_id": None,
        "base_model_id": None,
        "application_profile_name": None,
        "application_profile_arn": None,
        "underlying_quota_scope": None,
        "candidates": [],
        "unresolved_ref": None,
    }


def _extract_region_from_arn(arn: str) -> str | None:
    """Pull the region segment out of an AWS ARN."""
    parts = arn.split(":")
    if len(parts) >= 4 and parts[3]:
        return parts[3]
    return None


def resolve_profile_ref(
    ref: str, snapshot: dict | None
) -> ResolvedProfileRef:
    """Resolve a user-supplied Profile_Ref to a concrete CW_Model_ID and quota scope.

    Resolution order (first match wins):
      1. Application_Profile ARN regex match.
      2. System_Defined_Profile prefix (us./eu./ap./jp./global.).
      3. Case-insensitive exact match against Snapshot Application_Profile names.
      4. Foundation-model alias + trailing profile-family keyword.
      5. Bare foundation-model alias.
      6. Nothing matched -> kind="unresolved".
    """
    result = _empty_resolved_profile_ref()

    # Branch 1: Application_Profile ARN
    if _APP_PROFILE_ARN_RE.match(ref):
        result["kind"] = "application_profile"
        result["cw_model_id"] = ref
        result["application_profile_arn"] = ref
        if snapshot is not None:
            for app_profile, base_model_id in _iter_snapshot_app_profiles(snapshot):
                if app_profile.get("arn") == ref:
                    result["application_profile_name"] = app_profile.get("name")
                    result["base_model_id"] = base_model_id
                    result["underlying_quota_scope"] = _scope_from_wraps(
                        app_profile.get("wraps")
                    )
                    break
        return result

    # Branch 2: System_Defined_Profile prefix
    for prefix in GEO_PREFIXES:
        if ref.startswith(prefix):
            result["kind"] = "system_profile"
            result["cw_model_id"] = ref
            result["base_model_id"] = ref[len(prefix):]
            result["underlying_quota_scope"] = "cross-region"
            return result

    # Branch 3: case-insensitive Application_Profile name match
    if snapshot is not None:
        ref_lower = ref.lower()
        matches: list[tuple[dict, str | None]] = []
        for app_profile, base_model_id in _iter_snapshot_app_profiles(snapshot):
            name = app_profile.get("name")
            if name and name.lower() == ref_lower:
                matches.append((app_profile, base_model_id))

        if len(matches) == 1:
            app_profile, base_model_id = matches[0]
            result["kind"] = "application_profile"
            result["cw_model_id"] = app_profile.get("arn")
            result["application_profile_arn"] = app_profile.get("arn")
            result["application_profile_name"] = app_profile.get("name")
            result["base_model_id"] = base_model_id
            result["underlying_quota_scope"] = _scope_from_wraps(
                app_profile.get("wraps")
            )
            return result
        if len(matches) >= 2:
            result["kind"] = "ambiguous"
            labels = []
            for app_profile, base_model_id in matches:
                arn = app_profile.get("arn", "")
                region = _extract_region_from_arn(arn) or "unknown-region"
                wraps_target = base_model_id or "unknown"
                labels.append(
                    f"{app_profile.get('name')} "
                    f"({region}, wraps {wraps_target})"
                )
            result["candidates"] = labels
            return result

    # Branch 4: alias + trailing profile-family keyword
    tokens = ref.split()
    if len(tokens) >= 2:
        trailing = tokens[-1].lower()
        if trailing in _PROFILE_FAMILY_KEYWORDS:
            head = " ".join(tokens[:-1])
            base_model_id = resolve_model_id(head)
            if base_model_id:
                result["kind"] = "system_profile"
                result["cw_model_id"] = f"{trailing}.{base_model_id}"
                result["base_model_id"] = base_model_id
                result["underlying_quota_scope"] = "cross-region"
                return result

    # Branch 5: bare foundation-model alias
    bare_base_id = resolve_model_id(ref)
    if bare_base_id:
        result["kind"] = "model"
        result["cw_model_id"] = bare_base_id
        result["base_model_id"] = bare_base_id
        result["underlying_quota_scope"] = "on-demand"
        return result

    # Branch 5b: valid-looking Bedrock model ID not in catalog
    # (e.g., "anthropic.claude-sonnet-5" — new model not yet in models.py)
    if _BEDROCK_MODEL_ID_RE.match(ref):
        result["kind"] = "model"
        result["cw_model_id"] = ref
        result["base_model_id"] = ref
        result["underlying_quota_scope"] = "on-demand"
        return result

    # Branch 6: unresolved
    result["kind"] = "unresolved"
    result["unresolved_ref"] = ref
    return result
