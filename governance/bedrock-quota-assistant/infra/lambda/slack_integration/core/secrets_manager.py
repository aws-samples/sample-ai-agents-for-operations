"""Secrets Manager credential retrieval for Slack integration."""

import json
import os
from typing import Dict, Optional

import boto3
from botocore.exceptions import ClientError


class SlackCredentialsError(Exception):
    """Exception raised for Slack credential retrieval or validation errors."""

    pass


class SlackCredentialsManager:
    """
    Manages retrieval and validation of Slack credentials from AWS Secrets Manager.

    Caches credentials within Lambda execution context to minimize API calls.
    """

    def __init__(self):
        """Initialize the credentials manager."""
        self._credentials: Optional[Dict[str, str]] = None
        self._secrets_client = None

    def get_credentials(self) -> Dict[str, str]:
        """
        Retrieve Slack credentials from Secrets Manager.

        Returns:
            Dictionary with keys 'SLACK_BOT_TOKEN' and 'SLACK_SIGNING_SECRET'

        Raises:
            SlackCredentialsError: If credentials cannot be retrieved or are invalid
        """
        # Return cached credentials if available
        if self._credentials is not None:
            return self._credentials

        # Get Secrets Manager ARN (required)
        secret_arn = os.environ.get("SLACK_SECRET_ARN")

        if not secret_arn:
            raise SlackCredentialsError(
                "SLACK_SECRET_ARN environment variable is not set. "
                "Slack credentials must be stored in AWS Secrets Manager."
            )

        self._credentials = self._retrieve_from_secrets_manager(secret_arn)

        # Validate credentials before returning
        self._validate_credentials(self._credentials)

        return self._credentials

    def _retrieve_from_secrets_manager(self, secret_arn: str) -> Dict[str, str]:
        """
        Retrieve credentials from AWS Secrets Manager.

        Args:
            secret_arn: ARN of the secret to retrieve

        Returns:
            Dictionary with credential keys and values

        Raises:
            SlackCredentialsError: If retrieval or parsing fails
        """
        if self._secrets_client is None:
            self._secrets_client = boto3.client("secretsmanager")

        try:
            response = self._secrets_client.get_secret_value(SecretId=secret_arn)

            # Parse JSON secret
            secret_string = response.get("SecretString")
            if not secret_string:
                raise SlackCredentialsError(
                    "Secret does not contain SecretString field"
                )

            try:
                credentials = json.loads(secret_string)
                if not isinstance(credentials, dict):
                    raise SlackCredentialsError(
                        "Secret must be a JSON object (dictionary), "
                        f"got {type(credentials).__name__}"
                    )
            except json.JSONDecodeError as e:
                raise SlackCredentialsError(f"Failed to parse secret as JSON: {e}")

            return credentials

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            raise SlackCredentialsError(
                f"Failed to retrieve secret from Secrets Manager: {error_code}"
            )
        except SlackCredentialsError:
            # Re-raise our own exceptions
            raise
        except Exception as e:
            raise SlackCredentialsError(f"Unexpected error retrieving secret: {str(e)}")

    def _validate_credentials(self, credentials: Dict[str, str]) -> None:
        """
        Validate credential structure and values.

        Args:
            credentials: Dictionary to validate

        Raises:
            SlackCredentialsError: If validation fails
        """
        required_keys = ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"]

        for key in required_keys:
            if key not in credentials:
                raise SlackCredentialsError(f"Secret is missing required key: {key}")

            value = credentials[key]
            if not isinstance(value, str) or not value.strip():
                raise SlackCredentialsError(
                    f"Secret key '{key}' must be a non-empty string"
                )


# Module-level singleton instance
_credentials_manager = SlackCredentialsManager()


def get_slack_credentials() -> Dict[str, str]:
    """
    Get Slack credentials (module-level convenience function).

    Returns:
        Dictionary with keys 'SLACK_BOT_TOKEN' and 'SLACK_SIGNING_SECRET'

    Raises:
        SlackCredentialsError: If credentials cannot be retrieved or are invalid
    """
    return _credentials_manager.get_credentials()
