"""Conftest for slack integration stack tests.

Restores boto3 and botocore in sys.modules after tests in this directory
complete. Some test files in this directory replace boto3 with a MagicMock
at module level (to prevent the lambda handler from making real AWS calls
at import time). Without restoration, subsequent test files that import the
real boto3 would get the MagicMock instead.
"""

import sys

import pytest


@pytest.fixture(autouse=True, scope="session")
def restore_boto3_after_session():
    """Restore real boto3/botocore after all tests in this directory."""
    # Save before any test file in this dir can stomp them
    original_boto3 = sys.modules.get("boto3")
    original_botocore = sys.modules.get("botocore")
    original_botocore_exceptions = sys.modules.get("botocore.exceptions")
    yield
    # Restore after all tests in this directory complete
    if original_boto3 is not None:
        sys.modules["boto3"] = original_boto3
    elif "boto3" in sys.modules:
        del sys.modules["boto3"]
    if original_botocore is not None:
        sys.modules["botocore"] = original_botocore
    elif "botocore" in sys.modules:
        del sys.modules["botocore"]
    if original_botocore_exceptions is not None:
        sys.modules["botocore.exceptions"] = original_botocore_exceptions
    elif "botocore.exceptions" in sys.modules:
        del sys.modules["botocore.exceptions"]
