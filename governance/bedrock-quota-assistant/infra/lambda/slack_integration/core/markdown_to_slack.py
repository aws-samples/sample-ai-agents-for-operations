"""Convert standard markdown to Slack mrkdwn format.

Slack uses its own markup (mrkdwn) which differs from standard markdown.
This module converts the subset of markdown produced by the agent into
Slack-compatible formatting.

Conversions:
- **bold** → *bold*
- ## Headers → *Header* (bold text, Slack has no headers)
- [label](url) → <url|label>
- --- horizontal rules → unicode box-drawing line
- Bare URLs inside markdown links are left alone
- Code blocks (```) pass through unchanged (Slack supports them)
- _italic_ passes through unchanged (same syntax)
- `inline code` passes through unchanged (same syntax)
"""
import re

SLACK_DIVIDER = "━" * 25


def markdown_to_slack(text: str) -> str:
    """Convert markdown text to Slack mrkdwn format.

    Args:
        text: Markdown-formatted string

    Returns:
        Slack mrkdwn-formatted string
    """
    if not text:
        return text

    lines = text.split("\n")
    result = []
    in_code_block = False

    for line in lines:
        # Don't touch anything inside code blocks
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue

        if in_code_block:
            result.append(line)
            continue

        line = _convert_line(line)
        result.append(line)

    return "\n".join(result)


def _convert_line(line: str) -> str:
    """Convert a single non-code-block line from markdown to Slack mrkdwn."""
    stripped = line.strip()

    # Horizontal rules: --- or === (3+ chars)
    if re.match(r"^-{3,}$", stripped) or re.match(r"^={3,}$", stripped):
        return SLACK_DIVIDER

    # Headers: ## Text → *Text*
    header_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
    if header_match:
        header_text = header_match.group(2)
        # Convert any bold inside the header text first
        header_text = _convert_bold(header_text)
        return f"*{header_text}*"

    # Apply inline conversions
    line = _convert_markdown_links(line)
    line = _convert_bold(line)

    return line


def _convert_bold(text: str) -> str:
    """Convert **bold** to *bold*, avoiding already-single-star patterns."""
    # Match **text** but not inside backticks
    # Use a simple approach: replace **...** with *...*
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)


def _convert_markdown_links(text: str) -> str:
    """Convert [label](url) to <url|label>."""
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
