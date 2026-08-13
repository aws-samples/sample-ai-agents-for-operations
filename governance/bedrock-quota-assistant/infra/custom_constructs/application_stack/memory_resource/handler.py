"""Lambda handler for AgentCore Memory custom resource."""
import json
import boto3
import logging
import random
import time
import traceback

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock = boto3.client('bedrock-agentcore-control')

def calculate_backoff_delay(
    attempt: int,
    initial_delay: float = 2.0,
    max_delay: float = 30.0,
    multiplier: float = 2.0,
    jitter_percent: float = 0.25
) -> float:
    """
    Calculate exponential backoff delay with jitter.
    
    Args:
        attempt: Current attempt number (0-indexed)
        initial_delay: Starting delay in seconds
        max_delay: Maximum delay cap in seconds
        multiplier: Exponential growth factor
        jitter_percent: Randomization factor (0.0 to 1.0)
        
    Returns:
        float: Delay in seconds for this attempt
    """
    # Calculate base delay with exponential backoff
    base_delay = initial_delay * (multiplier ** attempt)
    
    # Cap at max_delay
    base_delay = min(base_delay, max_delay)
    
    # Apply jitter: randomize by ±jitter_percent
    jitter_range = base_delay * jitter_percent
    jitter = random.uniform(-jitter_range, jitter_range)
    
    # Ensure delay is never negative
    final_delay = max(0.0, base_delay + jitter)
    
    return final_delay

def wait_for_memory_status(
    bedrock_client,
    memory_id: str,
    target_statuses: list,
    max_duration_seconds: int = 240
):
    """
    Poll memory status until it reaches one of the target statuses.
    
    Args:
        bedrock_client: Boto3 bedrock-agentcore-control client
        memory_id: The memory resource ID to poll
        target_statuses: List of acceptable terminal statuses (e.g., ['ACTIVE'])
        max_duration_seconds: Maximum time to poll before timing out
        
    Returns:
        dict: The memory resource details when target status is reached
        
    Raises:
        TimeoutError: If polling exceeds max_duration_seconds
        Exception: If memory reaches FAILED status or unexpected error occurs
    """
    start_time = time.time()
    attempt = 0
    last_status = None
    
    logger.info(
        f"Starting status polling for memory {memory_id}",
        extra={
            "memory_id": memory_id,
            "target_statuses": target_statuses,
            "max_duration_seconds": max_duration_seconds
        }
    )
    
    while True:
        elapsed_time = time.time() - start_time
        
        # Check for timeout
        if elapsed_time >= max_duration_seconds:
            error_msg = (
                f"Timeout waiting for memory {memory_id} to reach target status. "
                f"Last status: {last_status}, Elapsed time: {elapsed_time:.2f}s"
            )
            logger.error(
                error_msg,
                extra={
                    "memory_id": memory_id,
                    "last_status": last_status,
                    "elapsed_time": elapsed_time,
                    "attempt_count": attempt
                }
            )
            raise TimeoutError(error_msg)
        
        try:
            # Log API request
            logger.info(
                f"Polling attempt {attempt}: Calling get_memory",
                extra={
                    "memory_id": memory_id,
                    "attempt": attempt,
                    "elapsed_time": elapsed_time
                }
            )
            
            # Call get_memory API
            response = bedrock_client.get_memory(memoryId=memory_id)
            memory = response.get('memory', {})
            current_status = memory.get('status')
            last_status = current_status
            
            # Log API response
            logger.info(
                f"Polling attempt {attempt}: Received status {current_status}",
                extra={
                    "memory_id": memory_id,
                    "status": current_status,
                    "attempt": attempt,
                    "elapsed_time": elapsed_time
                }
            )
            
            # Check if status is in target statuses
            if current_status in target_statuses:
                logger.info(
                    f"Memory {memory_id} reached target status {current_status}",
                    extra={
                        "memory_id": memory_id,
                        "status": current_status,
                        "elapsed_time": elapsed_time,
                        "attempt_count": attempt
                    }
                )
                return memory
            
            # Check for FAILED status
            if current_status == 'FAILED':
                failure_reason = memory.get('failureReason', 'Unknown failure reason')
                error_msg = (
                    f"Memory {memory_id} reached FAILED status. "
                    f"Reason: {failure_reason}"
                )
                logger.error(
                    error_msg,
                    extra={
                        "memory_id": memory_id,
                        "status": current_status,
                        "failure_reason": failure_reason,
                        "elapsed_time": elapsed_time,
                        "attempt_count": attempt
                    }
                )
                raise Exception(error_msg)
            
            # Check for unexpected statuses during Create/Update operations
            if current_status == 'DELETING' and 'DELETING' not in target_statuses:
                error_msg = (
                    f"Memory {memory_id} has unexpected status DELETING during operation"
                )
                logger.error(
                    error_msg,
                    extra={
                        "memory_id": memory_id,
                        "status": current_status,
                        "elapsed_time": elapsed_time,
                        "attempt_count": attempt
                    }
                )
                raise Exception(error_msg)
            
            # Status is in-progress (CREATING or DELETING), continue polling
            logger.info(
                f"Memory {memory_id} status is {current_status}, continuing to poll",
                extra={
                    "memory_id": memory_id,
                    "status": current_status,
                    "elapsed_time": elapsed_time,
                    "attempt": attempt
                }
            )
            
        except bedrock_client.exceptions.ResourceNotFoundException as e:
            # For delete operations, ResourceNotFoundException is expected
            if 'ResourceNotFoundException' in str(type(e).__name__):
                logger.info(
                    f"Memory {memory_id} not found (ResourceNotFoundException)",
                    extra={
                        "memory_id": memory_id,
                        "elapsed_time": elapsed_time,
                        "attempt_count": attempt
                    }
                )
                # Return a minimal response indicating the resource is gone
                return {"id": memory_id, "status": "DELETED"}
            raise
            
        except Exception as e:
            error_type = type(e).__name__
            
            # Classify errors as retryable or non-retryable
            retryable_errors = [
                'ThrottlingException',
                'InternalServerException', 
                'ServiceUnavailableException'
            ]
            
            non_retryable_errors = [
                'ValidationException',
                'AccessDeniedException',
                'ConflictException'
            ]
            
            # Log exception details with classification
            logger.error(
                f"Error during status polling for memory {memory_id}",
                extra={
                    "memory_id": memory_id,
                    "error_type": error_type,
                    "error_message": str(e),
                    "stack_trace": traceback.format_exc(),
                    "elapsed_time": elapsed_time,
                    "attempt_count": attempt,
                    "is_retryable": error_type in retryable_errors,
                    "is_non_retryable": error_type in non_retryable_errors
                }
            )
            
            # Handle retryable errors by continuing polling loop
            if error_type in retryable_errors:
                logger.warning(
                    f"Retryable error {error_type}, continuing to poll after backoff",
                    extra={
                        "memory_id": memory_id,
                        "error_type": error_type,
                        "attempt": attempt,
                        "elapsed_time": elapsed_time
                    }
                )
                # Continue to backoff and retry (don't raise, let loop continue)
            
            # Handle non-retryable errors by raising exception immediately
            elif error_type in non_retryable_errors:
                logger.error(
                    f"Non-retryable error {error_type}, failing immediately",
                    extra={
                        "memory_id": memory_id,
                        "error_type": error_type,
                        "error_message": str(e),
                        "attempt": attempt,
                        "elapsed_time": elapsed_time
                    }
                )
                raise
            
            # Unknown error type, raise to be safe
            else:
                logger.error(
                    f"Unknown error type {error_type}, failing immediately",
                    extra={
                        "memory_id": memory_id,
                        "error_type": error_type,
                        "error_message": str(e),
                        "attempt": attempt,
                        "elapsed_time": elapsed_time
                    }
                )
                raise
        
        # Calculate backoff delay and sleep
        delay = calculate_backoff_delay(attempt)
        logger.info(
            f"Waiting {delay:.2f}s before next polling attempt",
            extra={
                "memory_id": memory_id,
                "delay_seconds": delay,
                "attempt": attempt,
                "elapsed_time": elapsed_time
            }
        )
        time.sleep(delay)
        attempt += 1

def on_event(event, context):
    logger.info(f"Received event: {json.dumps(event)}")
    
    request_type = event['RequestType']
    props = event.get('ResourceProperties', {})
    
    try:
        if request_type == 'Create':
            return on_create(props)
        elif request_type == 'Update':
            return on_update(event['PhysicalResourceId'], props)
        elif request_type == 'Delete':
            return on_delete(event['PhysicalResourceId'])
        else:
            raise Exception(f"Unknown request type: {request_type}")
    except Exception as e:
        logger.warning(f"Error in {request_type} operation: {str(e)}")
        # For DELETE operations, return success to avoid blocking stack deletion
        if request_type == 'Delete':
            logger.warning(f"DELETE failed but returning success to unblock CloudFormation")
            return {
                'PhysicalResourceId': event.get('PhysicalResourceId', 'unknown')
            }
        # For CREATE/UPDATE, re-raise to signal failure
        raise

def on_create(props):
    logger.info("Creating AgentCore Memory resource")
    
    memory_name = props.get('MemoryName')
    # Replace hyphens with underscores to match API requirements: [a-zA-Z][a-zA-Z0-9_]{0,47}
    memory_name = memory_name.replace('-', '_')
    # Truncate to 48 characters max (API constraint)
    if len(memory_name) > 48:
        memory_name = memory_name[:48]
    description = props.get('Description', '')
    
    try:
        # Log API request parameters
        logger.info(
            "Calling create_memory API",
            extra={
                "memory_name": memory_name,
                "description": description
            }
        )
        
        # Create memory using AgentCore Control API
        response = bedrock.create_memory(
            name=memory_name,
            description=description,
            eventExpiryDuration=90  # Default to 90 days
        )
        
        memory_id = response['memory']['id']
        memory_arn = response['memory']['arn']
        
        logger.info(
            f"create_memory API returned successfully",
            extra={
                "memory_id": memory_id,
                "memory_arn": memory_arn
            }
        )
        
        # Wait for memory to reach ACTIVE status
        logger.info(f"Waiting for memory {memory_id} to reach ACTIVE status")
        memory = wait_for_memory_status(
            bedrock,
            memory_id,
            target_statuses=['ACTIVE'],
            max_duration_seconds=240
        )
        
        # Extract final memory details
        memory_id = memory['id']
        memory_arn = memory['arn']
        
        logger.info(f"Memory {memory_id} is now ACTIVE")
        
        return {
            'PhysicalResourceId': memory_id,
            'Data': {
                'MemoryId': memory_id,
                'MemoryArn': memory_arn
            }
        }
        
    except Exception as e:
        error_type = type(e).__name__
        
        # Handle ConflictException for retry idempotency
        if error_type == 'ConflictException':
            logger.warning(
                f"ConflictException during create_memory, checking if memory already exists",
                extra={
                    "memory_name": memory_name,
                    "error_message": str(e)
                }
            )
            
            try:
                # Try to get the existing memory by name
                # Note: We need to use the memory name as the ID since that's how AgentCore works
                get_response = bedrock.get_memory(memoryId=memory_name)
                existing_memory = get_response.get('memory', {})
                existing_status = existing_memory.get('status')
                
                logger.info(
                    f"Found existing memory with status: {existing_status}",
                    extra={
                        "memory_id": existing_memory.get('id'),
                        "status": existing_status
                    }
                )
                
                # If existing memory is ACTIVE, return it
                if existing_status == 'ACTIVE':
                    logger.info(
                        f"Existing memory is ACTIVE, returning existing memory details",
                        extra={
                            "memory_id": existing_memory['id'],
                            "memory_arn": existing_memory['arn']
                        }
                    )
                    return {
                        'PhysicalResourceId': existing_memory['id'],
                        'Data': {
                            'MemoryId': existing_memory['id'],
                            'MemoryArn': existing_memory['arn']
                        }
                    }
                else:
                    # Existing memory is not ACTIVE, raise exception
                    error_msg = (
                        f"Memory {memory_name} already exists but is not ACTIVE. "
                        f"Current status: {existing_status}"
                    )
                    logger.warning(
                        error_msg,
                        extra={
                            "memory_id": existing_memory.get('id'),
                            "status": existing_status
                        }
                    )
                    raise Exception(error_msg)
                    
            except bedrock.exceptions.ResourceNotFoundException:
                # Memory doesn't exist, re-raise original ConflictException
                logger.warning(
                    f"ConflictException but memory not found, re-raising original error",
                    extra={
                        "memory_name": memory_name,
                        "error_type": error_type,
                        "error_message": str(e)
                    }
                )
                raise e
        
        # Log all other errors
        logger.warning(
            f"Error creating memory",
            extra={
                "memory_name": memory_name,
                "error_type": error_type,
                "error_message": str(e),
                "stack_trace": traceback.format_exc()
            }
        )
        raise

def on_update(physical_id, props):
    logger.info(f"Updating AgentCore Memory resource: {physical_id}")
    
    description = props.get('Description', '')
    
    try:
        # Check current memory state before applying update (for idempotency)
        logger.info(
            "Checking current memory state before update",
            extra={
                "memory_id": physical_id
            }
        )
        
        get_response = bedrock.get_memory(memoryId=physical_id)
        current_memory = get_response.get('memory', {})
        current_description = current_memory.get('description', '')
        current_status = current_memory.get('status')
        
        logger.info(
            f"Current memory state retrieved",
            extra={
                "memory_id": physical_id,
                "current_status": current_status,
                "current_description": current_description,
                "new_description": description
            }
        )
        
        # Check if update is needed (idempotency check)
        if current_description == description:
            logger.info(
                f"Memory {physical_id} already has the desired description, no update needed",
                extra={
                    "memory_id": physical_id,
                    "description": description
                }
            )
            
            # If already ACTIVE, return immediately
            if current_status == 'ACTIVE':
                return {
                    'PhysicalResourceId': physical_id,
                    'Data': {
                        'MemoryId': physical_id,
                        'MemoryArn': current_memory['arn']
                    }
                }
            else:
                # Wait for ACTIVE status if not already there
                logger.info(f"Memory {physical_id} not ACTIVE, waiting for ACTIVE status")
                memory = wait_for_memory_status(
                    bedrock,
                    physical_id,
                    target_statuses=['ACTIVE'],
                    max_duration_seconds=240
                )
                return {
                    'PhysicalResourceId': physical_id,
                    'Data': {
                        'MemoryId': physical_id,
                        'MemoryArn': memory['arn']
                    }
                }
        
        # Log API request parameters
        logger.info(
            "Calling update_memory API",
            extra={
                "memory_id": physical_id,
                "description": description
            }
        )
        
        # Update memory description
        response = bedrock.update_memory(
            memoryId=physical_id,
            description=description
        )
        
        memory_id = response['memory']['id']
        memory_arn = response['memory']['arn']
        
        logger.info(
            f"update_memory API returned successfully",
            extra={
                "memory_id": memory_id,
                "memory_arn": memory_arn
            }
        )
        
        # Wait for memory to reach ACTIVE status
        logger.info(f"Waiting for memory {memory_id} to reach ACTIVE status")
        memory = wait_for_memory_status(
            bedrock,
            memory_id,
            target_statuses=['ACTIVE'],
            max_duration_seconds=240
        )
        
        # Extract final memory details
        memory_id = memory['id']
        memory_arn = memory['arn']
        
        logger.info(f"Memory {memory_id} is now ACTIVE after update")
        
        return {
            'PhysicalResourceId': memory_id,
            'Data': {
                'MemoryId': memory_id,
                'MemoryArn': memory_arn
            }
        }
    except bedrock.exceptions.ResourceNotFoundException as e:
        logger.error(
            f"Memory {physical_id} not found during update",
            extra={
                "memory_id": physical_id,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "stack_trace": traceback.format_exc()
            }
        )
        raise
    except bedrock.exceptions.ValidationException as e:
        logger.error(
            f"Validation error during update",
            extra={
                "memory_id": physical_id,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "stack_trace": traceback.format_exc()
            }
        )
        raise
    except Exception as e:
        logger.error(
            f"Error updating memory",
            extra={
                "memory_id": physical_id,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "stack_trace": traceback.format_exc()
            }
        )
        raise

def on_delete(physical_id):
    logger.info(f"Deleting AgentCore Memory resource: {physical_id}")
    
    try:
        # Log API request parameters
        logger.info(
            "Calling delete_memory API",
            extra={
                "memory_id": physical_id
            }
        )
        
        # Delete memory using PhysicalResourceId
        bedrock.delete_memory(memoryId=physical_id)
        
        logger.info(
            f"delete_memory API returned successfully",
            extra={
                "memory_id": physical_id
            }
        )
        
        # Wait for memory to be deleted (poll until ResourceNotFoundException)
        logger.info(f"Waiting for memory {physical_id} to be deleted")
        # Poll while status is DELETING, expecting ResourceNotFoundException
        memory = wait_for_memory_status(
            bedrock,
            physical_id,
            target_statuses=['DELETED'],  # wait_for_memory_status returns this when ResourceNotFoundException occurs
            max_duration_seconds=240
        )
        
        logger.info(f"Successfully deleted memory: {physical_id}")
        
        return {
            'PhysicalResourceId': physical_id
        }
        
    except bedrock.exceptions.ResourceNotFoundException:
        # Memory already deleted (idempotency)
        logger.info(
            f"Memory {physical_id} not found, assuming already deleted",
            extra={
                "memory_id": physical_id
            }
        )
        return {
            'PhysicalResourceId': physical_id
        }
        
    except bedrock.exceptions.ValidationException as e:
        # Log validation error but return success
        logger.error(
            f"Validation error during delete",
            extra={
                "memory_id": physical_id,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "stack_trace": traceback.format_exc()
            }
        )
        logger.warning(f"Returning success to unblock CloudFormation")
        return {
            'PhysicalResourceId': physical_id
        }
        
    except Exception as e:
        # Log all errors but return success to avoid blocking CloudFormation
        logger.error(
            f"Error deleting memory",
            extra={
                "memory_id": physical_id,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "stack_trace": traceback.format_exc()
            }
        )
        logger.warning(f"Returning success despite error to unblock CloudFormation")
        return {
            'PhysicalResourceId': physical_id
        }
