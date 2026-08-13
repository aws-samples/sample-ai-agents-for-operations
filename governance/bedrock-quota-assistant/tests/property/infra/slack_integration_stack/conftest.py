"""Shared test configuration for API Gateway Stack property tests.

This module sets up mocks that are shared across all property test modules
to avoid conflicts when pytest collects tests. After importing the handler
with mocked dependencies, it restores sys.modules so tests collected later
(outside this directory) are not affected.
"""

import json
import sys
import os
from unittest.mock import Mock, MagicMock

# Save originals before any mocking
_original_boto3 = sys.modules.get('boto3')
_original_botocore = sys.modules.get('botocore')
_original_botocore_exceptions = sys.modules.get('botocore.exceptions')


# Create a mock secrets manager client that returns proper values
def create_mock_boto3_client(service_name, *args, **kwargs):
    mock_client = Mock()
    if service_name == 'secretsmanager':
        mock_response = {
            'SecretString': json.dumps({
                'SLACK_BOT_TOKEN': 'xoxb-test-token-12345',
                'SLACK_SIGNING_SECRET': 'test-signing-secret-32chars-long'
            })
        }
        mock_client.get_secret_value = Mock(return_value=mock_response)
    return mock_client


# Mock botocore with ClientError
class MockClientError(Exception):
    def __init__(self, error_response, operation_name):
        self.response = error_response
        self.operation_name = operation_name
        super().__init__(f"An error occurred ({error_response['Error']['Code']}) when calling the {operation_name} operation")


mock_botocore = MagicMock()
mock_botocore.exceptions = MagicMock()
mock_botocore.exceptions.ClientError = MockClientError
sys.modules['botocore'] = mock_botocore
sys.modules['botocore.exceptions'] = mock_botocore.exceptions

mock_boto3 = Mock()
mock_boto3.client = Mock(side_effect=create_mock_boto3_client)
sys.modules['boto3'] = mock_boto3

# Mock Slack SDK modules
sys.modules['slack_bolt'] = MagicMock()
sys.modules['slack_bolt.adapter'] = MagicMock()
sys.modules['slack_bolt.adapter.aws_lambda'] = MagicMock()
sys.modules['slack_sdk'] = MagicMock()

# Set required environment variables
os.environ.setdefault('AGENTCORE_ARN', 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test')
os.environ.setdefault('AGENTCORE_REGION', 'us-west-2')
os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('AWS_LAMBDA_FUNCTION_NAME', 'test-function')
os.environ.setdefault('SLACK_SECRET_ARN', 'arn:aws:secretsmanager:us-west-2:123456789012:secret:test-secret')
# Required: the handler refuses to start without it, because event deduplication
# bounds how many Amazon Bedrock invocations a retried Slack webhook can trigger.
os.environ.setdefault('DEDUP_TABLE_NAME', 'test-dedup-table')

# Add lambda directory to path for imports
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
lambda_path = str(_PROJECT_ROOT / "infra" / "lambda" / "slack_integration")
if lambda_path not in sys.path:
    sys.path.insert(0, lambda_path)

# Purge handler and secrets_manager so they reimport with mocked boto3
sys.modules.pop('handler', None)
sys.modules.pop('core.secrets_manager', None)

# Import handler while mocks are active — test modules do `import handler`
# and rely on it being cached in sys.modules with mocked credentials
import handler  # noqa: F401, E402

# Restore or remove boto3/botocore so tests outside this directory aren't affected.
# The handler and core.* modules are already cached in sys.modules with their
# internal references to the mocked boto3 — they don't need it in sys.modules.
if _original_boto3 is not None:
    sys.modules['boto3'] = _original_boto3
else:
    sys.modules.pop('boto3', None)

if _original_botocore is not None:
    sys.modules['botocore'] = _original_botocore
else:
    sys.modules.pop('botocore', None)

if _original_botocore_exceptions is not None:
    sys.modules['botocore.exceptions'] = _original_botocore_exceptions
else:
    sys.modules.pop('botocore.exceptions', None)
