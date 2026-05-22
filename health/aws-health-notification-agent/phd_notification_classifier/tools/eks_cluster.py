# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""EKS cluster tools — describe and upgrade clusters via boto3.

IAM Permissions (defined in aha_eventbridge_lambda/template.yaml - AgentCoreRole):
    - eks:DescribeCluster — scoped to arn:aws:eks:${Region}:${AccountId}:cluster/*
    - eks:ListClusters — requires Resource: "*" (AWS service limitation)
    - eks:UpdateClusterVersion — NOT granted by default; must be explicitly added (see README "Mutating Actions")

Risk Assessment Reference:
    See security/SECURITY.md for full threat model. EKS upgrade operations (T-004)
    are gated behind human approval tokens (M-016). Cluster names are extracted
    from AWS Health event payloads only (never user input).
"""

from __future__ import annotations

import logging
import os

import boto3
from strands import tool

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "eu-west-1")


@tool
def describe_eks_cluster(cluster_name: str, region: str = "") -> dict:
    """Describe an EKS cluster to get its current version, status, and endpoint.

    Args:
        cluster_name: The name of the EKS cluster.
        region: AWS region (defaults to AWS_REGION env var or eu-west-1).

    Returns:
        Dict with cluster name, version, status, endpoint, platform_version,
        and kubernetes_network_config.
    """
    r = region or REGION
    eks = boto3.client("eks", region_name=r)
    try:
        resp = eks.describe_cluster(name=cluster_name)
        c = resp["cluster"]
        return {
            "cluster_name": c["name"],
            "version": c["version"],
            "status": c["status"],
            "endpoint": c.get("endpoint", ""),
            "platform_version": c.get("platformVersion", ""),
            "created_at": str(c.get("createdAt", "")),
            "region": r,
        }
    except Exception as exc:
        return {"error": str(exc), "cluster_name": cluster_name, "region": r}


@tool
def upgrade_eks_cluster(cluster_name: str, target_version: str, region: str = "") -> dict:
    """Upgrade an EKS cluster to a target Kubernetes version.

    This initiates the control plane upgrade. The upgrade typically takes
    15-30 minutes to complete.

    Args:
        cluster_name: The name of the EKS cluster to upgrade.
        target_version: The target Kubernetes version (e.g., "1.31").
        region: AWS region (defaults to AWS_REGION env var or eu-west-1).

    Returns:
        Dict with status, update_id, and cluster details on success,
        or error details on failure.
    """
    r = region or REGION
    eks = boto3.client("eks", region_name=r)
    try:
        resp = eks.update_cluster_version(name=cluster_name, version=target_version)
        update = resp["update"]
        return {
            "status": "initiated",
            "update_id": update["id"],
            "update_status": update["status"],
            "cluster_name": cluster_name,
            "target_version": target_version,
            "region": r,
            "message": f"Cluster upgrade initiated for {cluster_name} to version {target_version}. "
                       f"Update ID: {update['id']}. This typically takes 15-30 minutes.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "cluster_name": cluster_name,
            "target_version": target_version,
            "region": r,
        }
