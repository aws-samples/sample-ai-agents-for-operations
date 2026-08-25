"""Pytest configuration and shared fixtures for MIO Agent tests."""

from __future__ import annotations

import os

import pytest

# Set environment variables before any imports to avoid real AWS calls
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")


@pytest.fixture
def sample_account_id() -> str:
    """Standard test account ID — does not represent a real AWS account."""
    return "123456789012"


@pytest.fixture
def sample_role_arn(sample_account_id: str) -> str:
    """Standard test IAM role ARN."""
    return f"arn:aws:iam::{sample_account_id}:role/MIOAgentReadOnly"
