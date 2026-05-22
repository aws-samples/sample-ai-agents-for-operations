# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""MCP Tool Discovery — connects directly to the EKS MCP server via stdio transport.

Runs the open-source awslabs.eks-mcp-server as a subprocess (no Gateway needed).

Environment variables:
    EKS_MCP_ENABLED  — Set to "true" to enable the local EKS MCP server (optional)
    AWS_REGION       — Region for EKS API calls (defaults to eu-west-1)
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

EKS_MCP_ENABLED_ENV = "EKS_MCP_ENABLED"

VALID_AWS_REGIONS = {
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-central-2",
    "eu-north-1", "eu-south-1", "eu-south-2",
    "ap-south-1", "ap-south-2", "ap-southeast-1", "ap-southeast-2",
    "ap-southeast-3", "ap-northeast-1", "ap-northeast-2", "ap-northeast-3",
    "ap-east-1", "ca-central-1", "sa-east-1", "me-south-1", "me-central-1",
    "af-south-1", "il-central-1",
}


def _validate_aws_region(region: str) -> str:
    """Validate AWS region against known regions to prevent command injection."""
    if region in VALID_AWS_REGIONS:
        return region
    logger.warning("Invalid AWS_REGION '%s', defaulting to eu-west-1", region)
    return "eu-west-1"


def discover_mcp_tools() -> list:
    """Discover MCP tools from the local EKS MCP server via stdio transport.

    Connects to awslabs.eks-mcp-server using StdioServerParameters and returns
    the discovered tools. Returns an empty list if EKS_MCP_ENABLED is not set
    to "true" or if the connection fails.
    """
    eks_mcp_enabled = os.environ.get(EKS_MCP_ENABLED_ENV, "").lower() == "true"
    if not eks_mcp_enabled:
        logger.info("EKS_MCP_ENABLED not set — skipping local EKS MCP server")
        return []

    aws_region = _validate_aws_region(os.environ.get("AWS_REGION", "eu-west-1"))

    try:
        from strands.tools.mcp import MCPClient
        from mcp import StdioServerParameters

        server_params = StdioServerParameters(
            command="uvx",
            args=[
                # Legal approval: Apache 2.0 license, AWS Labs open source.
                # Security review: See security/SECURITY.md "3rd Party Service Approvals".
                "awslabs.eks-mcp-server@0.1.3",
                "--allow-write",
                "--allow-sensitive-data-access",
            ],
            env={
                "AWS_REGION": aws_region,
                "FASTMCP_LOG_LEVEL": "ERROR",
            },
        )

        from mcp.client.stdio import stdio_client

        eks_mcp_client = MCPClient(
            lambda: stdio_client(server_params),
            startup_timeout=60,
        )
        eks_mcp_client.start()

        start = time.monotonic()
        tools = eks_mcp_client.list_tools_sync()
        elapsed = time.monotonic() - start
        names = [getattr(t, 'tool_name', getattr(t, 'name', str(t))) for t in tools]
        logger.info("EKS MCP (local): discovered %d tools in %.2fs: %s", len(tools), elapsed, names)
        return tools
    except Exception:
        logger.exception("Local EKS MCP server failed to start or discover tools")
        return []
