# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Centralized configuration for Bedrock Quota Agent."""

import os
import logging

import boto3
from botocore.config import Config

# Initialize logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Initialize boto3 client for SSM
# Use AWS_REGION environment variable or default to us-east-1
# AgentCore sets AWS_REGION based on the runtime's region
ssm_region = os.environ.get('AWS_REGION', 'us-east-1')
ssm_client = boto3.client('ssm', region_name=ssm_region)


def _get_ssm_parameter(parameter_name: str) -> str:
    """Retrieve parameter value from SSM Parameter Store."""
    try:
        response = ssm_client.get_parameter(Name=parameter_name)
        return response['Parameter']['Value']
    except Exception as e:
        logger.warning(f"Failed to retrieve SSM parameter {parameter_name}: {e}")
        raise


# AgentCore Memory configuration - read from SSM Parameter Store at module load
try:
    AGENTCORE_MEMORY_ID = _get_ssm_parameter("/bedrock-quota-agent/memory-id")
    AGENTCORE_REGION = _get_ssm_parameter("/bedrock-quota-agent/region")
    logger.info(f"Successfully loaded configuration from SSM: memory_id={AGENTCORE_MEMORY_ID}, region={AGENTCORE_REGION}")
except Exception as e:
    logger.warning(f"Failed to load SSM parameters: {e}. Agent will run without memory support.")
    AGENTCORE_MEMORY_ID = None
    AGENTCORE_REGION = ssm_region


def _get_cache_table_name() -> str:
    """Get cache table name from SSM, with fallbacks to env var and default."""
    try:
        table_name = _get_ssm_parameter("/bedrock-quota-agent/cache-table-name")
        logger.info(f"Using cache table name from SSM: {table_name}")
        return table_name
    except Exception as e:
        logger.warning(f"Failed to load cache table name from SSM: {e}")
        env_value = os.environ.get("QUOTA_CACHE_TABLE")
        if env_value:
            logger.info(f"Using cache table name from environment variable: {env_value}")
            return env_value
        logger.warning("Using default cache table name: bedrock-quota-codes")
        return "bedrock-quota-codes"


# Default region for tool parameters
DEFAULT_REGION = AGENTCORE_REGION or os.environ.get('AWS_REGION', 'us-east-1')

# DynamoDB quota code cache configuration
QUOTA_CACHE_TABLE = _get_cache_table_name()

# Boto3 adaptive retry for Service Quotas API
_RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})
