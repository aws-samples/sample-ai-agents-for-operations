# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Tool: list_available_bedrock_models — lists foundation models available in the account."""

import logging

import boto3
from strands import tool

from config import DEFAULT_REGION
from models import get_model_info

logger = logging.getLogger(__name__)


@tool
def list_available_bedrock_models(region: str = DEFAULT_REGION, provider: str = None) -> str:
    """List available Bedrock models, optionally filtered by provider.

    Args:
        region: AWS region (default: agent's deployed region)
        provider: Optional provider filter (e.g., "Anthropic", "Amazon", "Meta")

    Returns:
        String with list of available Bedrock models
    """
    try:
        # Create regional Bedrock client
        regional_bedrock = boto3.client('bedrock', region_name=region)

        response = regional_bedrock.list_foundation_models()

        models_info = []
        models_info.append(f"Available Bedrock Models in {region}:")
        if provider:
            models_info.append(f"(Filtered by provider: {provider})")
        models_info.append("=" * 80)

        for model in response.get('modelSummaries', []):
            model_provider = model.get('providerName', 'Unknown')

            # Filter by provider if specified
            if provider and provider.lower() not in model_provider.lower():
                continue

            model_id = model.get('modelId', 'Unknown')
            model_name = model.get('modelName', 'Unknown')

            # Check if we have friendly name info
            catalog_info = get_model_info(model_id)
            friendly_aliases = ""
            if catalog_info and catalog_info.get('aliases'):
                friendly_aliases = f" (aliases: {', '.join(catalog_info['aliases'][:2])})"

            models_info.append(f"\n{model_name} ({model_provider}){friendly_aliases}")
            models_info.append(f"  Model ID: {model_id}")

            # Show input/output modalities
            input_modalities = model.get('inputModalities', [])
            output_modalities = model.get('outputModalities', [])
            if input_modalities:
                models_info.append(f"  Input: {', '.join(input_modalities)}")
            if output_modalities:
                models_info.append(f"  Output: {', '.join(output_modalities)}")

        return "\n".join(models_info)

    except Exception as e:
        logger.error(f"Error listing Bedrock models: {e}", exc_info=True)
        return f"Error listing Bedrock models: {str(e)}\n\nNote: Make sure you have proper AWS credentials and permissions for bedrock:ListFoundationModels"
