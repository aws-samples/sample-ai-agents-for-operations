# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Tool: draft_quota_increase_request — generates a ready-to-file quota increase request."""

import json as _json
import logging
import re

from strands import tool

from models import resolve_model_id, get_model_info
from helpers.quota_cache import (
    _query_quota_codes,
    _fetch_live_quota_values,
    _filter_strict_model_match,
)

logger = logging.getLogger(__name__)

# Module-level state shared with submit_quota_increase_case
_last_draft_data = None


@tool
def draft_quota_increase_request(
    model_id: str,
    region: str,
    use_case: str,
    desired_tpm: int,
    desired_rpm: int,
    steady_state_tpm: int,
    peak_tpm: int,
    steady_state_rpm: int,
    peak_rpm: int,
    avg_input_tokens: int,
    avg_output_tokens: int,
    input_modality: str = "text",
    output_modality: str = "text",
    justification: str = None,
    supporting_evidence: str = None,
) -> str:
    """Generate a complete, ready-to-file quota increase request for Bedrock.

    Fetches current quota values automatically and produces a structured request
    body that can be copy-pasted into the AWS Support Center console or submitted
    via the AWS CLI command included in the output. Does NOT submit anything — read-only.

    IMPORTANT: Before calling this tool, the agent should have already:
    1. Checked utilization via check_quota_utilization
    2. Gathered use case details from the user
    3. For low-utilization requests: built a justification explaining why the increase
       is needed despite low usage (new region, new account, anticipated growth, etc.)
    4. Confirmed all values with the user

    Args:
        model_id: Bedrock model ID or friendly name (e.g., "claude sonnet 4", "nova pro")
        region: AWS region the quota increase is for (e.g., "us-west-2")
        use_case: Business use case and why the increase is needed. Should be specific and quantified (e.g., "AI coding assistant for 2,000 engineers" not "we need more quota")
        desired_tpm: Requested new TPM (tokens per minute) limit
        desired_rpm: Requested new RPM (requests per minute) limit
        steady_state_tpm: Expected steady-state tokens per minute
        peak_tpm: Expected peak tokens per minute
        steady_state_rpm: Expected steady-state requests per minute
        peak_rpm: Expected peak requests per minute
        avg_input_tokens: Average input tokens per request
        avg_output_tokens: Average output tokens per request
        input_modality: Input modality - "text" or "image". Default: "text"
        output_modality: Output modality - "text" or "image". Default: "text"
        justification: Why the increase is needed when current utilization is low. Include context like new region expansion, new account deployment, or anticipated growth. Optional — omit if utilization is already high.
        supporting_evidence: Usage data from another region or account that demonstrates demand. For example: "Currently at 85% TPM in us-west-2 with peak of 170K TPM over the last 7 days." Optional.

    Returns:
        Formatted request with case body, CLI command, and console links ready for submission
    """
    global _last_draft_data

    # Resolve friendly model name
    resolved = resolve_model_id(model_id)
    if not resolved:
        resolved = model_id
    model_info = get_model_info(resolved)
    display_name = model_info["name"] if model_info else resolved

    # Fetch current quota values
    current_quotas = ""
    current_tpm = None
    current_rpm = None
    quota_codes = None
    values = None
    try:
        quota_codes = _query_quota_codes(display_name)
        if quota_codes:
            values = _fetch_live_quota_values(region, quota_codes[:10])
            if values:
                # Strict filter: exclude sub-version matches (e.g., "sonnet 4.5" when asking for "sonnet 4")
                values = _filter_strict_model_match(values, display_name)

                lines = []
                for q in values:
                    if q["value"] == "THROTTLED":
                        continue
                    unit = q['unit'] if q['unit'] and q['unit'] != 'None' else ''
                    lines.append(f"  {q['name']}: {q['value']}{' ' + unit if unit else ''}")
                    name_lower = q["name"].lower()
                    # Prefer non-cross-region quotas for current limits
                    if "tokens per minute" in name_lower and "cross-region" not in name_lower:
                        current_tpm = q["value"]
                    elif "requests per minute" in name_lower and "cross-region" not in name_lower:
                        current_rpm = q["value"]
                    # Fall back to cross-region if no direct quotas found
                    elif "tokens per minute" in name_lower and "cross-region" in name_lower and current_tpm is None:
                        current_tpm = q["value"]
                    elif "requests per minute" in name_lower and "cross-region" in name_lower and current_rpm is None:
                        current_rpm = q["value"]
                current_quotas = "\n".join(lines)
    except Exception as e:
        logger.warning(f"Could not fetch current quotas for draft: {e}")

    # Check if quotas are adjustable
    adjustable_note = ""
    try:
        if quota_codes and values:
            non_adjustable = [q for q in values if not q.get("adjustable", True) and q["value"] != "THROTTLED"]
            if non_adjustable:
                names = ", ".join(q["name"] for q in non_adjustable)
                adjustable_note = f"\n⚠️  Note: The following quotas are NOT adjustable: {names}\n"
    except Exception:
        pass

    subject = f"Bedrock quota increase: {display_name} in {region}"

    # Build the request body
    body_parts = [
        f"Requesting a quota increase for Amazon Bedrock model: {display_name}",
        f"Model ID: {resolved}",
        f"Region: {region}",
        "",
        "== Use Case ==",
        use_case,
        "",
        "== Requested Limits ==",
        f"Desired TPM: {desired_tpm:,}" + (f"  (current: {current_tpm:,.0f})" if current_tpm else ""),
        f"Desired RPM: {desired_rpm:,}" + (f"  (current: {current_rpm:,.0f})" if current_rpm else ""),
        "",
        "== Expected Usage Pattern ==",
        f"Steady-state TPM: {steady_state_tpm:,}",
        f"Peak TPM: {peak_tpm:,}",
        f"Steady-state RPM: {steady_state_rpm:,}",
        f"Peak RPM: {peak_rpm:,}",
        "",
        "== Request Profile ==",
        f"Avg. input tokens per request: {avg_input_tokens:,}",
        f"Avg. output tokens per request: {avg_output_tokens:,}",
        f"Input modality: {input_modality}",
        f"Output modality: {output_modality}",
    ]

    if current_quotas:
        body_parts.extend(["", "== Current Quotas ==", current_quotas])

    if justification:
        body_parts.extend(["", "== Justification ==", justification])

    if supporting_evidence:
        body_parts.extend(["", "== Supporting Evidence ==", supporting_evidence])

    body = "\n".join(body_parts)

    # Build the output with submission instructions
    result = [
        "=" * 70,
        "QUOTA INCREASE REQUEST DRAFT",
        "=" * 70,
        "",
        f"Subject: {subject}",
        "",
        body,
    ]

    if adjustable_note:
        result.append(adjustable_note)

    # Determine request strength
    strength_note = ""
    if current_tpm and current_rpm:
        tpm_util = (peak_tpm / current_tpm * 100) if current_tpm > 0 else 0
        rpm_util = (peak_rpm / current_rpm * 100) if current_rpm > 0 else 0
        max_util = max(tpm_util, rpm_util)
        if max_util >= 60:
            strength_note = f"Strong request: utilization at {max_util:.0f}%."
        elif justification:
            strength_note = f"Low utilization ({max_util:.0f}%) — justification included."
        else:
            strength_note = f"Low utilization ({max_util:.0f}%) — add justification to avoid delays."
    elif justification:
        strength_note = "No utilization data — justification included."
    else:
        strength_note = "No utilization data, no justification — request may be delayed."

    # Build CLI commands — file-based (shown in appendix) and inline (available on request)
    shell_body = body.replace("'", "'\\''")
    cli_base = (
        f"aws support create-case"
        f" --subject '{subject}'"
    )
    cli_flags = (
        " --service-code service-service-quotas"
        " --category-code general"
        " --severity-code normal"
        " --issue-type customer-service"
        " --language en"
        " --region us-east-1"
    )
    cli_command_inline = cli_base + f" --communication-body '{shell_body}'" + cli_flags
    cli_command_file = cli_base + " --communication-body file://case-body.txt" + cli_flags

    # Store full draft for post-LLM appendix (case body, CLI, links).
    # The LLM only sees the summary below — the actionable content is appended
    # verbatim by _append_draft_actions() after the LLM responds, preventing
    # the LLM from summarizing or paraphrasing the case body and CLI command.
    _last_draft_data = {
        "subject": subject,
        "case_body": body,
        "cli_command_file": cli_command_file,
        "cli_command_inline": cli_command_inline,
        "links": {
            "support_console": "https://console.aws.amazon.com/support/home#/case/create",
            "service_quotas": f"https://console.aws.amazon.com/servicequotas/home?region={region}#!/services/bedrock/quotas",
        },
        "note": "Requires a Business or Enterprise Support plan. Ask me for the full inline CLI command if you prefer not to use a file.",
    }

    # Return only the summary to the LLM — no case body, no CLI, no links.
    llm_response = {
        "status": "draft_ready",
        "summary": f"Draft quota increase request for {display_name} in {region}. {strength_note}",
        "subject": subject,
        "model": display_name,
        "region": region,
        "desired_tpm": desired_tpm,
        "desired_rpm": desired_rpm,
        "current_tpm": current_tpm,
        "current_rpm": current_rpm,
    }
    if adjustable_note:
        llm_response["adjustable_warning"] = adjustable_note.strip()
    return _json.dumps(llm_response)


def _append_draft_actions(response_text: str) -> str:
    """Strip LLM-generated draft content, then append the real case body/CLI/links.

    The LLM tends to fabricate its own case body, CLI command, and links even when
    told not to. This function:
    1. Keeps only the LLM's opening summary (first paragraph before any formatted content)
    2. Appends the authoritative content from _last_draft_data
    3. If the case was already submitted via submit_quota_increase_case, skips
       submission instructions and shows the case ID instead.

    Returns cleaned response_text with appendix, or unchanged if no draft exists.
    """
    global _last_draft_data

    if _last_draft_data is None:
        return response_text

    try:
        draft = _last_draft_data
        submitted = draft.get('submitted', False)
        case_id = draft.get('case_id')
        links = draft.get('links', {})
        note = draft.get('note', '')
        case_body = draft.get('case_body', '')
        cli_command_file = draft.get('cli_command_file', '')

        # If the case was already submitted, don't append submission instructions
        if submitted and case_id:
            return response_text

        # Aggressive stripping: keep only the LLM's opening summary.
        # The LLM generates a short summary then adds formatted content
        # (case body, CLI, links) using various formats (---, ===, ```, **, ##, etc).
        # We truncate at the first line that looks like structured content.
        lines = response_text.split('\n')
        keep_lines = []
        for line in lines:
            stripped = line.strip()
            # Stop at any line that signals the start of formatted/structured content
            if re.match(r'^(={3,}|-{3,}|#{1,3}\s|```|>\s|\*\*Option|\*\*Case|\*\*Submit|\*\*AWS CLI|\*\*Direct|\d+\.\s+(Go to|Open|Copy|Select|Click))', stripped):
                break
            # Also stop at lines containing console URLs or CLI commands
            if re.search(r'console\.aws\.amazon\.com|aws support create-case|service-code|SUBMISSION|CASE BODY', stripped, re.IGNORECASE):
                break
            keep_lines.append(line)

        # Trim trailing blank lines
        while keep_lines and not keep_lines[-1].strip():
            keep_lines.pop()

        cleaned = '\n'.join(keep_lines).rstrip()

        # Build the authoritative appendix
        support_url = links.get('support_console', '')
        quotas_url = links.get('service_quotas', '')

        appendix_parts = [
            "",
            "",
            "━" * 25,
            "",
            "*How to Submit*",
            "",
            "*Option 1: Ask me to submit it*",
            "Just say *\"submit it\"* or *\"raise the case\"* and I'll create the support case for you directly via the AWS Support API. Requires a Business or Enterprise Support plan.",
            "",
            "*Option 2: Support Console*",
            f"Open <{support_url}|AWS Support Console>, create a case under *Account and billing*, select *Service Quotas* as the service, and paste the case body below into the description.",
            "",
            "*Option 3: AWS CLI*",
            "Save the case body below to a file (e.g., `case-body.txt`) and run:",
            "```",
            cli_command_file,
            "```",
            f"_{note}_" if note else "",
            "",
            "*Option 4: Service Quotas Console (limited)*",
            f"Open <{quotas_url}|Service Quotas Console> to request increases directly. Note: you can only request one quota at a time with no description or justification, which may result in slower approval.",
            "",
            "━" * 25,
            "",
            "*Case body:*",
            "```",
            case_body,
            "```",
        ]

        return cleaned + "\n".join(appendix_parts)
    except Exception as e:
        logger.warning(f"Could not build draft appendix: {e}")
        return response_text
    finally:
        _last_draft_data = None
