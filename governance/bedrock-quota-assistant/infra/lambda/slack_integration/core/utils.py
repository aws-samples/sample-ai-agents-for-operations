"""Shared utilities for error handling and logging."""
import json
import logging
import re


class StructuredFormatter(logging.Formatter):
    """Format log records as JSON for CloudWatch Logs Insights."""
    
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        
        # Add optional context fields if present
        if hasattr(record, 'request_id'):
            log_data['request_id'] = record.request_id
        if hasattr(record, 'session_id'):
            log_data['session_id'] = record.session_id
        if hasattr(record, 'actor_id'):
            log_data['actor_id'] = record.actor_id
        if hasattr(record, 'error'):
            log_data['error'] = record.error
        if hasattr(record, 'duration_ms'):
            log_data['duration_ms'] = record.duration_ms
        
        return json.dumps(log_data)


def setup_structured_logging():
    """Configure structured logging for the Lambda function.
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Apply structured formatter to all handlers
    for handler in logger.handlers:
        handler.setFormatter(StructuredFormatter())
    
    return logger


def sanitize_error_message(error_msg: str) -> str:
    """Sanitize error messages to remove sensitive information.
    
    Removes:
    - AWS account IDs (12-digit numbers)
    - ARNs (arn:aws:...)
    - Internal stack traces (file paths)
    
    Args:
        error_msg: The original error message
        
    Returns:
        Sanitized error message safe for external display
    """
    # Remove ARNs first (before account IDs to avoid partial matches)
    sanitized = re.sub(r'arn:aws:[a-z0-9\-]+:[a-z0-9\-]*:\d{12}:[^\s]+', '[ARN]', error_msg)
    
    # Remove AWS account IDs (12-digit numbers)
    sanitized = re.sub(r'\b\d{12}\b', '[ACCOUNT_ID]', sanitized)
    
    # Remove file paths (common in stack traces)
    sanitized = re.sub(r'/[a-zA-Z0-9_\-./]+\.py', '[FILE]', sanitized)
    sanitized = re.sub(r'File "[^"]+",', 'File "[FILE]",', sanitized)
    
    # Remove line numbers from stack traces (with or without comma)
    sanitized = re.sub(r',?\s*line \d+', ' line [LINE]', sanitized)
    
    return sanitized
