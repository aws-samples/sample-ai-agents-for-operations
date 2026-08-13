# Dockerfile for Bedrock Quota Agent - AgentCore Deployment
# This builds a container for deployment to AWS Bedrock AgentCore
# Includes ADOT instrumentation for observability

# NOTE: For production, replace with internal ECR-hosted base image
FROM --platform=linux/arm64 python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install ADOT SDK for observability
RUN pip install --no-cache-dir aws-opentelemetry-distro>=0.10.1

# Copy the agent code
COPY agent.py config.py models.py ./
COPY tools/ ./tools/
COPY helpers/ ./helpers/
COPY prompts/ ./prompts/

# Expose port 8080 (required by AgentCore)
EXPOSE 8080

# Set environment variables for AWS and observability
# AWS_REGION will be set by AgentCore runtime based on deployment region
ENV PYTHONUNBUFFERED=1

# AgentCore Memory ID will be provided at runtime via environment variable
# ENV AGENTCORE_MEMORY_ID will be set by the runtime configuration

# DynamoDB quota code cache table
ENV QUOTA_CACHE_TABLE=bedrock-quota-codes

# ADOT/OpenTelemetry configuration for AgentCore observability
ENV AGENT_OBSERVABILITY_ENABLED=true
ENV OTEL_PYTHON_DISTRO=aws_distro
ENV OTEL_PYTHON_CONFIGURATOR=aws_configurator
ENV OTEL_METRICS_EXPORTER=awsemf
ENV OTEL_TRACES_EXPORTER=otlp
ENV OTEL_LOGS_EXPORTER=otlp
ENV OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
ENV OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true
ENV OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true
ENV OTEL_LOG_LEVEL=info

# Switch to non-root user
USER agent

# Health check for the /ping endpoint
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')"

# Run the agent with ADOT auto-instrumentation
CMD ["opentelemetry-instrument", "python", "agent.py"]
