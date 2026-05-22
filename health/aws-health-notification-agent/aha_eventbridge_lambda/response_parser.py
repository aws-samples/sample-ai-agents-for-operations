# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.

"""Shared response parsing utilities for AgentCore streaming responses."""

import json
import logging

logger = logging.getLogger(__name__)


def unwrap_response(raw: str) -> str:
    """Unwrap double-JSON-encoded agent response."""
    text = raw.strip()
    if text.startswith('"'):
        try:
            text = json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def read_streaming_response(response: dict, event_arn: str) -> str:
    """Read all chunks from the AgentCore streaming response.

    Args:
        response: The raw boto3 response from invoke_agent_runtime().
        event_arn: The event ARN, used for log context.

    Returns:
        The concatenated response string.

    Raises:
        RuntimeError: If the stream is interrupted before all chunks are read.
    """
    content_type = response.get("contentType", "")
    chunks: list[str] = []

    try:
        if "text/event-stream" in content_type:
            for line in response["response"].iter_lines(chunk_size=10):
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data: "):
                        decoded = decoded[6:]
                    chunks.append(decoded)
        else:
            for chunk in response.get("response", []):
                chunks.append(chunk.decode("utf-8"))
    except Exception as exc:
        partial = "".join(chunks)
        logger.warning(
            json.dumps({
                "message": "Streaming response interrupted",
                "event_arn": event_arn,
                "partial_response": partial,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })
        )
        raise RuntimeError(
            f"AgentCore streaming response interrupted: {exc}"
        ) from exc

    result = "\n".join(chunks) if "text/event-stream" in content_type else "".join(chunks)

    logger.info(
        json.dumps({
            "message": "AgentCore invocation complete",
            "event_arn": event_arn,
            "response_length": len(result),
        })
    )

    return result


def extract_json_from_text(text: str) -> dict:
    """Extract a JSON dict from the agent's final text using brace-matching.

    Args:
        text: Raw agent response text potentially containing JSON.

    Returns:
        Parsed dict from the text.

    Raises:
        ValueError: If no JSON dict can be found in the text.
    """
    import re

    # Strategy 1: markdown code block
    m = re.search(r'```(?:json)?\s*(\{)', text)
    if m:
        start = m.start(1)
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i+1])
                    except json.JSONDecodeError:
                        break
                    break

    # Strategy 2: find the first large JSON object
    start = text.find('{')
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    if len(candidate) > 50:
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, dict):
                                return parsed
                        except json.JSONDecodeError:
                            pass
                    break
        start = text.find('{', start + 1)

    # Strategy 3: whole text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    raise ValueError(f"No JSON dict found in text ({len(text)} chars)")
