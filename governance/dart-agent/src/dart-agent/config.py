"""
DART Agent Configuration

All configuration loaded from environment variables.
Secrets retrieved from AWS Secrets Manager — never hardcoded.
"""

import os
import json
import boto3
import logging

logger = logging.getLogger(__name__)


class Config:
    """Central configuration for DART Agent."""

    # ── AWS ──────────────────────────────────────────────────────────────────
    AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")
    AWS_ACCOUNT_ID: str = os.environ.get("AWS_ACCOUNT_ID", "")

    # ── Bedrock ───────────────────────────────────────────────────────────────
    MODEL_ID: str = os.environ.get(
        "MODEL_ID", "anthropic.claude-sonnet-4-20250514-v1:0"
    )
    MAX_TOKENS: int = int(os.environ.get("MAX_TOKENS", "4096"))

    # ── Agent behaviour ───────────────────────────────────────────────────────
    DEFAULT_MODE: str = os.environ.get("DEFAULT_MODE", "step-by-step")  # or "autonomous"
    DEDUP_SIMILARITY_THRESHOLD: float = float(
        os.environ.get("DEDUP_SIMILARITY_THRESHOLD", "0.85")
    )
    PII_REDACTION_STRATEGY: str = os.environ.get(
        "PII_REDACTION_STRATEGY", "mask"
    )  # mask | remove
    MIN_RESPONSE_TOKENS: int = int(os.environ.get("MIN_RESPONSE_TOKENS", "10"))
    MINHASH_NUM_PERM: int = int(os.environ.get("MINHASH_NUM_PERM", "128"))

    # ── Storage ───────────────────────────────────────────────────────────────
    OUTPUT_BUCKET: str = os.environ.get("OUTPUT_BUCKET", "")
    AUDIT_TABLE: str = os.environ.get("AUDIT_TABLE", "dart-agent-audit-trail")

    # ── Input validation / path safety (threat T-1, E-2) ──────────────────────
    # Comma-separated allowlists. Empty = allow any (documented sample default;
    # operators SHOULD restrict these in production).
    ALLOWED_S3_BUCKETS: list[str] = [
        b.strip() for b in os.environ.get("ALLOWED_S3_BUCKETS", "").split(",") if b.strip()
    ]
    ALLOWED_S3_PREFIXES: list[str] = [
        p.strip() for p in os.environ.get("ALLOWED_S3_PREFIXES", "").split(",") if p.strip()
    ]
    # Local dataset/output paths must resolve inside this directory when set.
    ALLOWED_LOCAL_BASE_DIR: str = os.environ.get("ALLOWED_LOCAL_BASE_DIR", "")

    # ── Observability ─────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    # ── PII entity types to detect ────────────────────────────────────────────
    PII_ENTITY_TYPES: list[str] = [
        "EMAIL",
        "PHONE",
        "NAME",
        "ADDRESS",
        "SSN",
        "CREDIT_DEBIT_NUMBER",
        "DATE_TIME",
        "IP_ADDRESS",
        "URL",
    ]

    # ── Known fine-tuning schema patterns ─────────────────────────────────────
    # Maps schema format name → required fields
    KNOWN_SCHEMAS: dict[str, list[str]] = {
        "instruction_response": ["instruction", "response"],
        "prompt_completion": ["prompt", "completion"],
        "chat_messages": ["messages"],
        "input_output": ["input", "output"],
        "question_answer": ["question", "answer"],
    }

    # ── Model token context limits ────────────────────────────────────────────
    MODEL_CONTEXT_LIMITS: dict[str, int] = {
        "anthropic.claude-sonnet-4-20250514-v1:0": 200000,
        "anthropic.claude-haiku-4-5": 200000,
        "amazon.nova-pro-v1:0": 300000,
        "meta.llama3-70b-instruct-v1:0": 128000,
        "meta.llama3-8b-instruct-v1:0": 128000,
        "mistral.mistral-7b-instruct-v0:2": 32768,
    }

    # ── Recommended minimum dataset sizes per model size ─────────────────────
    MODEL_MIN_RECORDS: dict[str, int] = {
        "7b": 500,
        "8b": 500,
        "13b": 1000,
        "34b": 2000,
        "70b": 3000,
        "default": 500,
    }

    @classmethod
    def validate(cls) -> None:
        """Validate required configuration at startup. Fail fast if missing."""
        required = ["OUTPUT_BUCKET", "AUDIT_TABLE", "AWS_REGION"]
        missing = [k for k in required if not getattr(cls, k)]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
        logger.info("Configuration validated successfully.")

    @staticmethod
    def get_secret(secret_name: str) -> dict:
        """Retrieve a secret from AWS Secrets Manager."""
        client = boto3.client("secretsmanager", region_name=Config.AWS_REGION)
        try:
            response = client.get_secret_value(SecretId=secret_name)
            return json.loads(response["SecretString"])
        except Exception as e:
            # Log at warning: the exception is re-raised and handled upstream
            # (semgrep logging-error-without-handling).
            logger.warning(f"Failed to retrieve secret {secret_name}: {e}")
            raise
