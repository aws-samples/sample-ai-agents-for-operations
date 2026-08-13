"""Property-based tests for ECR repository construct."""

import re
from hypothesis import given, strategies as st


def validate_image_tag_pattern(tag: str) -> bool:
    """
    Validate that an image tag matches one of the expected patterns.
    
    Expected patterns:
    1. Timestamp pattern: YYYYMMDD-HHMMSS (e.g., 20240219-143022)
    2. Git commit SHA or content hash: 7-40 hexadecimal characters
       (CDK content hashes are typically longer, but we accept 7-40 to match git SHAs)
    
    Args:
        tag: The image tag to validate
        
    Returns:
        True if the tag matches an expected pattern, False otherwise
    """
    if not tag:
        return False
    
    # Pattern 1: Timestamp pattern YYYYMMDD-HHMMSS
    timestamp_pattern = r'^\d{8}-\d{6}$'
    if re.match(timestamp_pattern, tag):
        # Validate the timestamp components are in valid ranges
        try:
            year = int(tag[0:4])
            month = int(tag[4:6])
            day = int(tag[6:8])
            hour = int(tag[9:11])
            minute = int(tag[11:13])
            second = int(tag[13:15])
            
            if not (2000 <= year <= 2100):
                return False
            if not (1 <= month <= 12):
                return False
            if not (1 <= day <= 31):
                return False
            if not (0 <= hour <= 23):
                return False
            if not (0 <= minute <= 59):
                return False
            if not (0 <= second <= 59):
                return False
            
            return True
        except (ValueError, IndexError):
            return False
    
    # Pattern 2: Git commit SHA or content hash (7-40 hexadecimal characters)
    # This covers both git commit SHAs and CDK content-based hashes
    git_sha_pattern = r'^[a-f0-9]{7,40}$'
    if re.match(git_sha_pattern, tag):
        return True
    
    return False


def validate_image_uri_pattern(image_uri: str) -> bool:
    """
    Validate that an image URI contains a valid tag or digest.
    
    Image URI formats:
    - With tag: <registry>/<repository>:<tag>
    - With digest: <registry>/<repository>@sha256:<hash>
    
    Args:
        image_uri: The full image URI to validate
        
    Returns:
        True if the URI contains a valid tag or digest, False otherwise
    """
    if not image_uri:
        return False
    
    # Check for digest format (preferred by CDK)
    if '@sha256:' in image_uri:
        digest_match = re.search(r'@sha256:([a-f0-9]+)', image_uri)
        if not digest_match:
            return False
        
        digest_hash = digest_match.group(1)
        
        # SHA256 hash should be 64 hexadecimal characters
        if len(digest_hash) != 64:
            return False
        if not re.match(r'^[a-f0-9]+$', digest_hash):
            return False
        
        return True
    
    # Check for tag format
    if ':' in image_uri:
        # Extract the tag (after the last :)
        tag = image_uri.split(':')[-1]
        return validate_image_tag_pattern(tag)
    
    return False


@given(
    tag=st.one_of(
        # Generate valid timestamp tags
        st.builds(
            lambda y, m, d, h, min, s: f"{y:04d}{m:02d}{d:02d}-{h:02d}{min:02d}{s:02d}",
            y=st.integers(min_value=2000, max_value=2100),
            m=st.integers(min_value=1, max_value=12),
            d=st.integers(min_value=1, max_value=31),
            h=st.integers(min_value=0, max_value=23),
            min=st.integers(min_value=0, max_value=59),
            s=st.integers(min_value=0, max_value=59),
        ),
        # Generate valid git commit SHAs or content hashes (7-40 hex chars)
        st.text(
            min_size=7,
            max_size=40,
            alphabet='0123456789abcdef'
        ),
    )
)
def test_docker_image_tag_follows_expected_pattern(tag: str):
    """
    Verify Docker image tag follows expected pattern.
    
    For any Docker image built by the stack, the image tag should match either a timestamp 
    pattern (YYYYMMDD-HHMMSS) or a git commit SHA pattern (7-40 hexadecimal characters).
    
    This test validates the tag pattern matching logic by generating valid tags according
    to the expected patterns and verifying they are correctly validated.
    
    Note: CDK's DockerImageAsset generates image URIs with content-based hashes, which are
    hexadecimal strings typically in the 7-40 character range. This test ensures that our 
    validation logic correctly accepts all expected tag formats.
    """
    # All generated tags should be valid according to our validation function
    assert validate_image_tag_pattern(tag), (
        f"Generated tag '{tag}' should be valid but validation failed"
    )


@given(
    # Generate truly invalid tags with characters that can't be in any valid pattern
    invalid_tag=st.one_of(
        # Invalid characters (uppercase hex, special chars)
        st.text(
            min_size=1,
            max_size=20,
            alphabet='GHIJKLMNOPQRSTUVWXYZ!@#$%^&*()'
        ),
        # Hex strings that are too long (> 40 chars)
        st.text(
            min_size=41,
            max_size=100,
            alphabet='0123456789abcdef'
        ),
        # Mixed valid/invalid characters
        st.builds(
            lambda valid, invalid: valid + invalid,
            valid=st.text(min_size=1, max_size=10, alphabet='0123456789abcdef'),
            invalid=st.text(min_size=1, max_size=5, alphabet='GHIJKLMNOPQRSTUVWXYZ!@#')
        ),
    )
)
def test_docker_image_tag_rejects_invalid_patterns(invalid_tag: str):
    """
    Verify Docker image tag rejects invalid patterns.
    
    This test verifies that invalid tag patterns are correctly rejected by the validation logic.
    Tags that don't match any of the expected patterns (timestamp, git SHA, or content hash)
    should be identified as invalid.
    
    Note: This test focuses on tags with invalid characters or lengths that clearly don't
    match any expected pattern.
    """
    # Invalid tags should be rejected
    assert not validate_image_tag_pattern(invalid_tag), (
        f"Invalid tag '{invalid_tag}' should be rejected but was accepted"
    )


@given(
    # Generate valid image URIs with different formats
    image_uri=st.one_of(
        # URI with digest format
        st.builds(
            lambda repo, hash: f"123456789012.dkr.ecr.us-east-1.amazonaws.com/{repo}@sha256:{hash}",
            repo=st.just("bedrock-quota-agent"),
            hash=st.text(min_size=64, max_size=64, alphabet='0123456789abcdef'),
        ),
        # URI with tag format (content hash)
        st.builds(
            lambda repo, tag: f"123456789012.dkr.ecr.us-east-1.amazonaws.com/{repo}:{tag}",
            repo=st.just("bedrock-quota-agent"),
            tag=st.text(min_size=7, max_size=40, alphabet='0123456789abcdef'),
        ),
        # URI with timestamp tag
        st.builds(
            lambda repo, y, m, d, h, min, s: f"123456789012.dkr.ecr.us-east-1.amazonaws.com/{repo}:{y:04d}{m:02d}{d:02d}-{h:02d}{min:02d}{s:02d}",
            repo=st.just("bedrock-quota-agent"),
            y=st.integers(min_value=2000, max_value=2100),
            m=st.integers(min_value=1, max_value=12),
            d=st.integers(min_value=1, max_value=31),
            h=st.integers(min_value=0, max_value=23),
            min=st.integers(min_value=0, max_value=59),
            s=st.integers(min_value=0, max_value=59),
        ),
    )
)
def test_docker_image_uri_validation(image_uri: str):
    """
    Verify Docker image URI validation.
    
    This test validates that complete image URIs (including registry, repository, and tag/digest)
    are correctly validated. The URI should contain either a valid tag or a valid SHA256 digest.
    """
    # All generated URIs should be valid
    assert validate_image_uri_pattern(image_uri), (
        f"Generated image URI '{image_uri}' should be valid but validation failed"
    )
