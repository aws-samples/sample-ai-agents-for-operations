# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

# Base image sourced from AWS Public ECR (public.ecr.aws) and pinned by digest
# for reproducible builds. For production, consider hosting the base image in a
# private registry you control.
FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# Build args
ARG ENVIRONMENT=dev

# Labels
LABEL org.opencontainers.image.title="DART Agent"
LABEL org.opencontainers.image.description="Dataset Audit & Readiness for Training — LLM pre-flight validation agent"
LABEL org.opencontainers.image.vendor="Amazon Web Services"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libgomp1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r dartuser && useradd -r -g dartuser -d /app -s /sbin/nologin dartuser

WORKDIR /app

# Copy and install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip==24.3.1 && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/dart-agent/ ./

# Set ownership to non-root user
RUN chown -R dartuser:dartuser /app

# Switch to non-root user
USER dartuser

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AWS_REGION=us-east-1 \
    LOG_LEVEL=INFO

# Health check (JSON/exec form so OS signals pass through — semgrep prefer-json-notation)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import agent; print('healthy')"]

# Entry point
ENTRYPOINT ["python", "agent.py"]
