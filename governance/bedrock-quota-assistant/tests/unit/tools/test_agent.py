"""
Unit tests for agent.py cache table name resolution.

Tests the _get_cache_table_name() function's fallback chain:
1. SSM parameter
2. Environment variable
3. Default value
"""

import os
import sys
import logging
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError




class TestCacheTableNameResolution:
    """Test suite for cache table name resolution logic."""

    def test_get_cache_table_name_from_ssm(self):
        """Test _get_cache_table_name() reads from SSM when available."""
        # Create a mock SSM client
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.return_value = {
            'Parameter': {'Value': 'bedrock-quota-codes-prod'}
        }
        
        # Create a mock logger
        MagicMock()
        
        # Patch boto3.client to return our mock
        with patch('boto3.client', return_value=mock_ssm_client):
            with patch.dict(os.environ, {'AWS_REGION': 'us-east-1'}, clear=True):
                # Import agent module fresh
                for mod in list(sys.modules):
                    if mod in ('agent', 'config'):
                        del sys.modules[mod]
                import config
                
                # Act
                result = config._get_cache_table_name()
                
                # Assert
                assert result == 'bedrock-quota-codes-prod'
                mock_ssm_client.get_parameter.assert_called()

    def test_get_cache_table_name_fallback_to_env_var(self):
        """Test fallback to environment variable when SSM fails."""
        # Create a mock SSM client that raises an error
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.side_effect = ClientError(
            {'Error': {'Code': 'ParameterNotFound', 'Message': 'Not found'}},
            'GetParameter'
        )
        
        # Patch boto3.client to return our mock
        with patch('boto3.client', return_value=mock_ssm_client):
            with patch.dict(os.environ, {'AWS_REGION': 'us-east-1', 'QUOTA_CACHE_TABLE': 'my-custom-table'}, clear=True):
                # Import agent module fresh
                for mod in list(sys.modules):
                    if mod in ('agent', 'config'):
                        del sys.modules[mod]
                import config
                
                # Act
                result = config._get_cache_table_name()
                
                # Assert
                assert result == 'my-custom-table'

    def test_get_cache_table_name_fallback_to_default(self):
        """Test fallback to default when both SSM and env var unavailable."""
        # Create a mock SSM client that raises an error
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.side_effect = ClientError(
            {'Error': {'Code': 'ParameterNotFound', 'Message': 'Not found'}},
            'GetParameter'
        )
        
        # Patch boto3.client to return our mock
        with patch('boto3.client', return_value=mock_ssm_client):
            with patch.dict(os.environ, {'AWS_REGION': 'us-east-1'}, clear=True):
                # Import agent module fresh
                for mod in list(sys.modules):
                    if mod in ('agent', 'config'):
                        del sys.modules[mod]
                import config
                
                # Act
                result = config._get_cache_table_name()
                
                # Assert
                assert result == 'bedrock-quota-codes'

    def test_appropriate_warnings_logged_for_ssm_failure(self, caplog):
        """Test appropriate warnings are logged when SSM fails."""
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.side_effect = ClientError(
            {'Error': {'Code': 'ParameterNotFound', 'Message': 'Not found'}},
            'GetParameter'
        )

        with patch('boto3.client', return_value=mock_ssm_client):
            with patch.dict(os.environ, {'AWS_REGION': 'us-east-1'}, clear=True):
                for mod in list(sys.modules):
                    if mod in ('agent', 'config'):
                        del sys.modules[mod]
                with caplog.at_level(logging.WARNING, logger='config'):
                    import config as cfg
                    cfg._get_cache_table_name()

                assert any('Failed to load cache table name from SSM' in r.message for r in caplog.records)

    def test_appropriate_warnings_logged_for_default_fallback(self, caplog):
        """Test appropriate warnings are logged when falling back to default."""
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.side_effect = ClientError(
            {'Error': {'Code': 'ParameterNotFound', 'Message': 'Not found'}},
            'GetParameter'
        )

        with patch('boto3.client', return_value=mock_ssm_client):
            with patch.dict(os.environ, {'AWS_REGION': 'us-east-1'}, clear=True):
                for mod in list(sys.modules):
                    if mod in ('agent', 'config'):
                        del sys.modules[mod]
                with caplog.at_level(logging.WARNING, logger='config'):
                    import config as cfg
                    cfg._get_cache_table_name()

                assert any('Using default cache table name: bedrock-quota-codes' in r.message for r in caplog.records)

    def test_info_logged_for_env_var_fallback(self, caplog):
        """Test info message is logged when using environment variable."""
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.side_effect = ClientError(
            {'Error': {'Code': 'ParameterNotFound', 'Message': 'Not found'}},
            'GetParameter'
        )

        with patch('boto3.client', return_value=mock_ssm_client):
            with patch.dict(os.environ, {'AWS_REGION': 'us-east-1', 'QUOTA_CACHE_TABLE': 'env-table'}, clear=True):
                for mod in list(sys.modules):
                    if mod in ('agent', 'config'):
                        del sys.modules[mod]
                with caplog.at_level(logging.INFO, logger='config'):
                    import config as cfg
                    cfg._get_cache_table_name()

                assert any('Using cache table name from environment variable: env-table' in r.message for r in caplog.records)
