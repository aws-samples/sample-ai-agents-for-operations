"""Pytest configuration for the test suite."""
import pytest
import sys
import os

# Add project root and src/ to sys.path so imports work:
# - Project root: enables `from infra.stacks...` imports for CDK tests
# - src/: enables `import agent`, `from helpers...`, `from tools...` imports
# - lambda/slack_integration/: enables `from core...`, `from adapters...` for lambda tests
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src_dir = os.path.join(_project_root, "src")
_infra_dir = os.path.join(_project_root, "infra")
_lambda_slack_dir = os.path.join(_project_root, "infra", "lambda", "slack_integration")

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
if _infra_dir not in sys.path:
    sys.path.insert(0, _infra_dir)
if _lambda_slack_dir not in sys.path:
    sys.path.insert(0, _lambda_slack_dir)


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests (requires AWS credentials and may incur costs)"
    )
    parser.addoption(
        "--retain-application-stack",
        action="store_true",
        default=False,
        help="Retain Application stack between test runs to speed up integration tests"
    )
    parser.addoption(
        "--retain-cache-stack",
        action="store_true",
        default=False,
        help="Retain Cache stack between test runs to speed up integration tests"
    )


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration test (requires --integration flag)"
    )
    config.addinivalue_line(
        "markers",
        "retain_stacks: mark test to retain Application stack for faster iteration (dev only)"
    )

    # Validate integration test configuration if --integration flag is provided
    if config.getoption("--integration"):
        # Import here to avoid import errors when not running integration tests
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "integration"))
        from config import validate_integration_test_config

        # Validate configuration (will exit if validation fails)
        validate_integration_test_config()


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless --integration flag is provided."""
    if not config.getoption("--integration"):
        skip_integration = pytest.mark.skip(reason="Integration tests require --integration flag")
        for item in items:
            # Only skip tests in the tests/integration/ directory
            if "tests/integration/" in item.nodeid:
                item.add_marker(skip_integration)
