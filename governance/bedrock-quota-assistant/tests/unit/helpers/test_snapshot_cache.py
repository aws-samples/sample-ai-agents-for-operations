"""
Unit tests for the module-level single-slot Snapshot cache in ``src/agent.py``.

Covers ``get_snapshot_cached()`` (Component 7 of the per-profile-metrics design)
with a mocked DynamoDB resource:

1. First call reads from DynamoDB (``get_item`` invoked exactly once, returns
   the item).
2. Second call returns the cached object without a second DynamoDB read
   (``get_item`` invoked only once across the two calls).
3. Missing-item response yields ``None`` (DynamoDB returns an empty dict with
   no ``"Item"`` key).
4. DynamoDB exception yields ``None`` (e.g., ``ClientError`` raised by
   ``get_item``).
5. Reset-on-handler-start clears the slot — setting
   ``helpers_snapshot._snapshot_cache = None`` causes the next ``get_snapshot_cached()``
   call to hit DynamoDB again.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

import helpers.snapshot as helpers_snapshot
from helpers.snapshot import get_snapshot_cached


@pytest.fixture(autouse=True)
def reset_snapshot_cache():
    """Reset ``helpers_snapshot._snapshot_cache`` before and after every test.

    The snapshot cache is module-level mutable state. Tests that populate it
    must not leak into sibling tests — hence an autouse fixture that zeros the
    slot both before yield (covers the case where a prior test failed
    mid-way) and after yield (covers the current test).
    """
    helpers_snapshot._snapshot_cache = None
    yield
    helpers_snapshot._snapshot_cache = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_dynamodb(get_item_return=None, get_item_side_effect=None):
    """Build a mock ``dynamodb`` resource whose ``.Table(...).get_item(...)``
    chain returns ``get_item_return`` or raises ``get_item_side_effect``.

    Returns a tuple of (mock_resource, mock_table) so tests can assert on
    ``mock_table.get_item.call_count`` and ``call_args``.
    """
    mock_table = MagicMock()
    if get_item_side_effect is not None:
        mock_table.get_item.side_effect = get_item_side_effect
    else:
        mock_table.get_item.return_value = get_item_return

    mock_resource = MagicMock()
    mock_resource.Table.return_value = mock_table
    return mock_resource, mock_table


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetSnapshotCached:
    """Tests for ``get_snapshot_cached()`` caching and error handling."""

    def test_first_call_reads_from_dynamodb(self):
        """First call performs exactly one ``get_item`` and returns the item."""
        snapshot_item = {
            "PK": "customer-profile",
            "SK": "latest",
            "models": [{"display_name": "Claude Sonnet 4.6"}],
        }
        mock_resource, mock_table = _make_mock_dynamodb(
            get_item_return={"Item": snapshot_item}
        )

        with patch("helpers.snapshot.boto3.resource", return_value=mock_resource):
            result = get_snapshot_cached()

        assert result == snapshot_item
        assert mock_table.get_item.call_count == 1
        mock_table.get_item.assert_called_once_with(
            Key={"PK": "customer-profile", "SK": "latest"}
        )

    def test_second_call_returns_cached_without_second_read(self):
        """Second call within the same invocation does not touch DynamoDB."""
        snapshot_item = {"PK": "customer-profile", "SK": "latest", "v": 1}
        mock_resource, mock_table = _make_mock_dynamodb(
            get_item_return={"Item": snapshot_item}
        )

        with patch("helpers.snapshot.boto3.resource", return_value=mock_resource):
            first = get_snapshot_cached()
            second = get_snapshot_cached()

        assert first is second, "cached object must be returned by identity"
        assert first == snapshot_item
        assert mock_table.get_item.call_count == 1, (
            "get_item should be called only once across two cached calls"
        )

    def test_missing_item_returns_none(self):
        """When DynamoDB returns a response with no ``Item`` key, yield ``None``."""
        mock_resource, mock_table = _make_mock_dynamodb(get_item_return={})

        with patch("helpers.snapshot.boto3.resource", return_value=mock_resource):
            result = get_snapshot_cached()

        assert result is None
        assert mock_table.get_item.call_count == 1
        # Cache slot must remain None so a later call retries DynamoDB.
        assert helpers_snapshot._snapshot_cache is None

    def test_dynamodb_exception_returns_none(self):
        """A raised ``ClientError`` from ``get_item`` yields ``None`` gracefully."""
        client_error = ClientError(
            {"Error": {"Code": "ResourceNotFoundException",
                       "Message": "Table not found"}},
            "GetItem",
        )
        mock_resource, mock_table = _make_mock_dynamodb(
            get_item_side_effect=client_error
        )

        with patch("helpers.snapshot.boto3.resource", return_value=mock_resource):
            result = get_snapshot_cached()

        assert result is None
        assert mock_table.get_item.call_count == 1
        assert helpers_snapshot._snapshot_cache is None

    def test_reset_on_handler_start_forces_dynamodb_reread(self):
        """Resetting ``_snapshot_cache`` to ``None`` makes the next call hit DynamoDB.

        Simulates the top-of-``agent_handler`` reset that prevents one turn's
        Snapshot from leaking into the next in a long-lived AgentCore
        container.
        """
        snapshot_item = {"PK": "customer-profile", "SK": "latest", "turn": 1}
        mock_resource, mock_table = _make_mock_dynamodb(
            get_item_return={"Item": snapshot_item}
        )

        with patch("helpers.snapshot.boto3.resource", return_value=mock_resource):
            # First invocation populates the cache.
            get_snapshot_cached()
            assert mock_table.get_item.call_count == 1

            # Simulate the reset that happens at the top of agent_handler.
            helpers_snapshot._snapshot_cache = None

            # Next call must go back to DynamoDB.
            get_snapshot_cached()
            assert mock_table.get_item.call_count == 2, (
                "after reset, get_snapshot_cached() must re-read DynamoDB"
            )
