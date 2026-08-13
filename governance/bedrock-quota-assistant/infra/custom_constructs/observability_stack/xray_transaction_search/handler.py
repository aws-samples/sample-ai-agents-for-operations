"""Lambda handler for X-Ray Transaction Search custom resource.

Manages X-Ray Transaction Search configuration as a CloudFormation custom resource.
Uses X-Ray APIs directly to handle the account-level singleton nature of Transaction
Search, avoiding AlreadyExists errors from the native AWS::XRay::TransactionSearchConfig.

Lifecycle:
    Create: Check if Transaction Search is already enabled. If yes, update indexing
            percentage to desired value. If no, enable it via update_trace_segment_destination
            and set the indexing percentage.
    Update: Update the indexing percentage via update_indexing_rule.
    Delete: Either no-op (retain) or disable by setting destination back to XRay,
            controlled by RetainOnDelete property.
"""

import json
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

xray = boto3.client("xray")


def on_event(event, context):
    """Route CloudFormation custom resource events."""
    logger.info(f"Received event: {json.dumps(event)}")

    request_type = event["RequestType"]
    props = event.get("ResourceProperties", {})

    if request_type == "Create":
        return on_create(props)
    elif request_type == "Update":
        return on_update(props)
    elif request_type == "Delete":
        return on_delete(event.get("PhysicalResourceId", "xray-transaction-search"), props)
    else:
        raise ValueError(f"Unknown request type: {request_type}")


def _get_desired_percentage(props):
    """Extract and validate indexing percentage from resource properties."""
    pct = float(props.get("IndexingPercentage", 1))
    if not (0 <= pct <= 100):
        raise ValueError(f"IndexingPercentage must be between 0 and 100, got {pct}")
    return pct


def _is_transaction_search_enabled():
    """Check if Transaction Search is currently enabled.

    Returns:
        tuple: (is_enabled: bool, current_destination: str)
    """
    try:
        response = xray.get_trace_segment_destination()
        destination = response.get("Destination", "XRay")
        status = response.get("Status", "")
        is_enabled = destination == "CloudWatchLogs" and status == "ACTIVE"
        logger.info(
            f"Transaction Search status: destination={destination}, "
            f"status={status}, is_enabled={is_enabled}"
        )
        return is_enabled, destination
    except Exception as e:
        logger.warning(f"Could not check Transaction Search status: {e}")
        return False, "Unknown"


def _enable_transaction_search():
    """Enable Transaction Search by setting trace destination to CloudWatch Logs."""
    logger.info("Enabling Transaction Search (destination -> CloudWatchLogs)")
    xray.update_trace_segment_destination(Destination="CloudWatchLogs")
    _wait_for_destination_status("CloudWatchLogs")


def _disable_transaction_search():
    """Disable Transaction Search by setting trace destination back to X-Ray."""
    logger.info("Disabling Transaction Search (destination -> XRay)")
    xray.update_trace_segment_destination(Destination="XRay")
    _wait_for_destination_status("XRay")


def _wait_for_destination_status(expected_destination, max_attempts=60, delay=10):
    """Poll get_trace_segment_destination until status is ACTIVE with expected destination.

    Note: AWS docs state it can take up to 10 minutes for Transaction Search
    changes to propagate. This function polls for up to ~10 minutes.

    Args:
        expected_destination: Expected Destination value ('CloudWatchLogs' or 'XRay')
        max_attempts: Maximum number of polling attempts (default 60)
        delay: Seconds between attempts (default 10)
    """
    import time

    for attempt in range(max_attempts):
        try:
            response = xray.get_trace_segment_destination()
            destination = response.get("Destination", "")
            status = response.get("Status", "")
            logger.info(
                f"Wait attempt {attempt + 1}/{max_attempts}: "
                f"destination={destination}, status={status}"
            )
            if destination == expected_destination and status == "ACTIVE":
                logger.info(
                    f"Destination reached {expected_destination} with ACTIVE status"
                )
                return
        except Exception as e:
            logger.warning(f"Error polling destination status: {e}")

        time.sleep(delay)

    logger.warning(
        f"Timed out waiting for destination={expected_destination} ACTIVE "
        f"after {max_attempts * delay}s, proceeding anyway"
    )


def _update_indexing_percentage(percentage):
    """Update the X-Ray indexing rule sampling percentage."""
    logger.info(f"Setting indexing percentage to {percentage}")
    xray.update_indexing_rule(
        Name="Default",
        Rule={"Probabilistic": {"DesiredSamplingPercentage": percentage}},
    )


def on_create(props):
    """Handle Create event.

    If Transaction Search is already enabled, just update the indexing percentage.
    If not enabled, enable it and set the indexing percentage.
    """
    desired_pct = _get_desired_percentage(props)
    is_enabled, _ = _is_transaction_search_enabled()

    if is_enabled:
        logger.info("Transaction Search already enabled, updating indexing percentage")
    else:
        logger.info("Transaction Search not enabled, enabling now")
        _enable_transaction_search()

    _update_indexing_percentage(desired_pct)

    return {
        "PhysicalResourceId": "xray-transaction-search",
        "Data": {
            "Status": "Enabled",
            "IndexingPercentage": str(desired_pct),
            "WasAlreadyEnabled": str(is_enabled),
        },
    }


def on_update(props):
    """Handle Update event.

    Update the indexing percentage. If Transaction Search somehow got disabled,
    re-enable it first.
    """
    desired_pct = _get_desired_percentage(props)
    is_enabled, _ = _is_transaction_search_enabled()

    if not is_enabled:
        logger.warning("Transaction Search was disabled, re-enabling")
        _enable_transaction_search()

    _update_indexing_percentage(desired_pct)

    return {
        "PhysicalResourceId": "xray-transaction-search",
        "Data": {
            "Status": "Enabled",
            "IndexingPercentage": str(desired_pct),
        },
    }


def on_delete(physical_id, props):
    """Handle Delete event.

    If RetainOnDelete is true (default), do nothing — leave Transaction Search enabled.
    If RetainOnDelete is false, disable Transaction Search by reverting destination to XRay.
    """
    retain = props.get("RetainOnDelete", "true").lower() == "true"

    if retain:
        logger.info("RetainOnDelete=true, leaving Transaction Search enabled")
    else:
        logger.info("RetainOnDelete=false, disabling Transaction Search")
        try:
            _disable_transaction_search()
        except Exception as e:
            # Don't fail the delete — log and move on
            logger.error(f"Failed to disable Transaction Search: {e}")

    return {"PhysicalResourceId": physical_id}
