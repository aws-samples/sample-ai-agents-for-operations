#!/usr/bin/env bash
# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.

set -euo pipefail

# ============================================================================
# AWS Health Notification Classifier - One-command deployment
#
# Fully idempotent: creates all resources via CloudFormation on first run,
# updates on subsequent runs. A single CloudFormation stack manages everything
# including the AgentCore Runtime. No SAM CLI required.
#
# Usage:
#   ./deploy.sh                                    # Minimal deploy (classification + SNS)
#   ./deploy.sh --slack-webhook "https://..."      # With Slack notifications
#   ./deploy.sh --destroy                          # Tear down all resources
#
# Full example:
#   ./deploy.sh \
#     --region eu-west-1 \
#     --slack-webhook "https://hooks.slack.com/triggers/..." \
#     --jira-url "https://myorg.atlassian.net" \
#     --jira-project "OPS" \
#     --jira-email "ops@company.com" \
#     --jira-secret-arn "arn:aws:secretsmanager:eu-west-1:123456789012:secret:jira-token" \
#     --remediation-mode approval \
#     --ses-sender "notifications@company.com" \
#     --ses-recipient "ops-team@company.com"
#
# Prerequisites:
#   - AWS CLI v2 configured with credentials
#   - Docker (for ARM64 container builds)
#   - Python 3.12+ with pip
#   - Amazon Bedrock model access enabled for Claude Sonnet
# ============================================================================

show_help() {
    cat << 'EOF'
Usage: ./deploy.sh [OPTIONS]

Options:
  --region REGION              AWS region (default: eu-west-1)
  --slack-webhook URL          Slack Workflow webhook URL for notifications
  --jira-url URL               Jira instance URL (e.g., https://myorg.atlassian.net)
  --jira-project KEY           Jira project key (default: OPS)
  --jira-issue-type TYPE       Jira issue type (default: Task)
  --jira-email EMAIL           Jira account email for API auth
  --jira-secret-arn ARN        Secrets Manager ARN for Jira API token
  --remediation-mode MODE      "approval" (SES email) or "notification" (default: notification)
  --require-routing-approval   Require Slack approval for routing config changes
  --ses-sender EMAIL           Verified SES sender email (for approval emails)
  --ses-recipient EMAIL        Notification recipient email
  --aha-event-bus NAME         Custom EventBridge bus for AHA events (default: none)
  --model-id ID                Bedrock model ID (default: auto-detected from region)
  --destroy                    Tear down ALL resources
  --help                       Show this help message

Environment variables are also supported as fallback (for CI/CD):
  AWS_DEFAULT_REGION, SLACK_WEBHOOK_URL, JIRA_BASE_URL, JIRA_PROJECT_KEY,
  JIRA_ISSUE_TYPE, JIRA_USER_EMAIL, JIRA_SECRET_ARN, REMEDIATION_MODE,
  REQUIRE_ROUTING_APPROVAL, SES_SENDER_EMAIL, NOTIFICATION_RECIPIENT_EMAIL,
  AHA_EVENT_BUS_NAME, BEDROCK_MODEL_ID
EOF
    exit 0
}

# ============================================================================
# Parse CLI arguments
# ============================================================================

ARG_REGION=""
ARG_SLACK_WEBHOOK=""
ARG_JIRA_URL=""
ARG_JIRA_PROJECT=""
ARG_JIRA_ISSUE_TYPE=""
ARG_JIRA_EMAIL=""
ARG_JIRA_SECRET_ARN=""
ARG_REMEDIATION_MODE=""
ARG_REQUIRE_ROUTING_APPROVAL=""
ARG_SES_SENDER=""
ARG_SES_RECIPIENT=""
ARG_AHA_EVENT_BUS=""
ARG_MODEL_ID=""
ARG_DESTROY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --region) ARG_REGION="$2"; shift 2 ;;
        --slack-webhook) ARG_SLACK_WEBHOOK="$2"; shift 2 ;;
        --jira-url) ARG_JIRA_URL="$2"; shift 2 ;;
        --jira-project) ARG_JIRA_PROJECT="$2"; shift 2 ;;
        --jira-issue-type) ARG_JIRA_ISSUE_TYPE="$2"; shift 2 ;;
        --jira-email) ARG_JIRA_EMAIL="$2"; shift 2 ;;
        --jira-secret-arn) ARG_JIRA_SECRET_ARN="$2"; shift 2 ;;
        --remediation-mode) ARG_REMEDIATION_MODE="$2"; shift 2 ;;
        --require-routing-approval) ARG_REQUIRE_ROUTING_APPROVAL="true"; shift ;;
        --ses-sender) ARG_SES_SENDER="$2"; shift 2 ;;
        --ses-recipient) ARG_SES_RECIPIENT="$2"; shift 2 ;;
        --aha-event-bus) ARG_AHA_EVENT_BUS="$2"; shift 2 ;;
        --model-id) ARG_MODEL_ID="$2"; shift 2 ;;
        --destroy) ARG_DESTROY=true; shift ;;
        --help|-h) show_help ;;
        *) echo "Unknown option: $1"; show_help ;;
    esac
done

# ============================================================================
# Resolve configuration (CLI flags > env vars > defaults)
# ============================================================================

REGION="${ARG_REGION:-${AWS_DEFAULT_REGION:-eu-west-1}}"
STACK_NAME="aha-eventbridge-lambda"
ECR_REPO_NAME="phd-notification-classifier"

SLACK_WEBHOOK_URL="${ARG_SLACK_WEBHOOK:-${SLACK_WEBHOOK_URL:-}}"
JIRA_BASE_URL="${ARG_JIRA_URL:-${JIRA_BASE_URL:-}}"
JIRA_PROJECT_KEY="${ARG_JIRA_PROJECT:-${JIRA_PROJECT_KEY:-OPS}}"
JIRA_ISSUE_TYPE="${ARG_JIRA_ISSUE_TYPE:-${JIRA_ISSUE_TYPE:-Task}}"
JIRA_USER_EMAIL="${ARG_JIRA_EMAIL:-${JIRA_USER_EMAIL:-}}"
JIRA_SECRET_ARN="${ARG_JIRA_SECRET_ARN:-${JIRA_SECRET_ARN:-}}"
REMEDIATION_MODE="${ARG_REMEDIATION_MODE:-${REMEDIATION_MODE:-notification}}"
REQUIRE_ROUTING_APPROVAL="${ARG_REQUIRE_ROUTING_APPROVAL:-${REQUIRE_ROUTING_APPROVAL:-false}}"
SES_SENDER="${ARG_SES_SENDER:-${SES_SENDER_EMAIL:-}}"
NOTIFICATION_EMAIL="${ARG_SES_RECIPIENT:-${NOTIFICATION_RECIPIENT_EMAIL:-}}"
AHA_EVENT_BUS="${ARG_AHA_EVENT_BUS:-${AHA_EVENT_BUS_NAME:-}}"

# Derive model ID from region if not explicitly set
if [ -n "$ARG_MODEL_ID" ]; then
    BEDROCK_MODEL_ID="$ARG_MODEL_ID"
elif [ -n "${BEDROCK_MODEL_ID:-}" ]; then
    BEDROCK_MODEL_ID="$BEDROCK_MODEL_ID"
else
    case "$REGION" in
        eu-*) BEDROCK_MODEL_ID="eu.anthropic.claude-sonnet-4-6" ;;
        us-*) BEDROCK_MODEL_ID="us.anthropic.claude-sonnet-4-6" ;;
        ap-*) BEDROCK_MODEL_ID="ap.anthropic.claude-sonnet-4-6" ;;
        *)    BEDROCK_MODEL_ID="anthropic.claude-sonnet-4-6" ;;
    esac
fi

# Verify AWS CLI is available
if ! command -v aws &>/dev/null; then
    echo "ERROR: AWS CLI not found. Install from: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

# Get account ID
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text --no-cli-pager 2>/dev/null || echo '')}"
if [ -z "$AWS_ACCOUNT_ID" ] || [ "$AWS_ACCOUNT_ID" = "None" ]; then
    echo "ERROR: Could not determine AWS account ID. Check your credentials."
    exit 1
fi

ECR_REPO="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO_NAME}"

echo ""
echo "==> AWS Health Notification Classifier"
echo "    Region:      $REGION"
echo "    Account:     $AWS_ACCOUNT_ID"
echo "    Model:       $BEDROCK_MODEL_ID"
echo "    AHA bus:     ${AHA_EVENT_BUS:-(not configured)}"
echo "    Slack:       ${SLACK_WEBHOOK_URL:-(not configured)}"
echo "    Jira:        ${JIRA_BASE_URL:-(not configured)}"
echo "    Remediation: $REMEDIATION_MODE"
echo ""

# ============================================================================
# Destroy
# ============================================================================

if [ "$ARG_DESTROY" = true ]; then
    echo "==> DESTROYING all resources..."

    # Empty S3 bucket (versioned)
    BUCKET="phd-routing-config-${AWS_ACCOUNT_ID}"
    if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
        echo "    Emptying S3 bucket: $BUCKET..."
        aws s3 rm "s3://${BUCKET}" --recursive --region "$REGION" --no-cli-pager 2>/dev/null || true
        # Delete versions and markers
        aws s3api list-object-versions --bucket "$BUCKET" --region "$REGION" --no-cli-pager --output json 2>/dev/null | \
          python3 -c "
import json, sys, subprocess
data = json.load(sys.stdin)
objects = [{'Key':v['Key'],'VersionId':v['VersionId']} for v in data.get('Versions',[])]
objects += [{'Key':m['Key'],'VersionId':m['VersionId']} for m in data.get('DeleteMarkers',[])]
if objects:
    subprocess.run(['aws','s3api','delete-objects','--bucket','${BUCKET}','--delete',json.dumps({'Objects':objects,'Quiet':True}),'--region','${REGION}','--no-cli-pager'],capture_output=True)
    print(f'    Deleted {len(objects)} versioned objects.')
" 2>/dev/null || true
    fi

    # Delete stacks
    echo "    Deleting stack: $STACK_NAME..."
    aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION" --no-cli-pager 2>/dev/null || true
    aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION" --no-cli-pager 2>/dev/null || true

    echo "    Deleting stack: phd-security-foundation..."
    aws cloudformation delete-stack --stack-name phd-security-foundation --region "$REGION" --no-cli-pager 2>/dev/null || true
    aws cloudformation wait stack-delete-complete --stack-name phd-security-foundation --region "$REGION" --no-cli-pager 2>/dev/null || true

    # Delete ECR
    echo "    Deleting ECR: $ECR_REPO_NAME..."
    aws ecr delete-repository --repository-name "$ECR_REPO_NAME" --force --region "$REGION" --no-cli-pager 2>/dev/null || true

    # Delete deployment artifacts bucket
    ARTIFACT_BUCKET="phd-deploy-artifacts-${AWS_ACCOUNT_ID}"
    if aws s3api head-bucket --bucket "$ARTIFACT_BUCKET" --region "$REGION" 2>/dev/null; then
        echo "    Deleting artifacts bucket: $ARTIFACT_BUCKET..."
        aws s3 rm "s3://${ARTIFACT_BUCKET}" --recursive --region "$REGION" --no-cli-pager 2>/dev/null || true
        aws s3api delete-bucket --bucket "$ARTIFACT_BUCKET" --region "$REGION" --no-cli-pager 2>/dev/null || true
    fi

    echo ""
    echo "==> Teardown complete. Run ./deploy.sh to redeploy."
    exit 0
fi

# ============================================================================
# Deploy
# ============================================================================

# Step 1: Ensure ECR repo exists and has an image
echo "==> Step 1: ECR repository..."
if aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$REGION" --no-cli-pager &>/dev/null; then
    echo "    ECR exists: $ECR_REPO_NAME"
else
    echo "    Creating ECR: $ECR_REPO_NAME..."
    aws ecr create-repository --repository-name "$ECR_REPO_NAME" --region "$REGION" --no-cli-pager >/dev/null
fi

# Step 2: Build and push container
echo "==> Step 2: Building and pushing container..."
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo 'latest')}"
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
docker build --platform linux/arm64 -t "${ECR_REPO_NAME}:${IMAGE_TAG}" .
docker tag "${ECR_REPO_NAME}:${IMAGE_TAG}" "$ECR_REPO:${IMAGE_TAG}"
docker tag "${ECR_REPO_NAME}:${IMAGE_TAG}" "$ECR_REPO:latest"
docker push "$ECR_REPO:${IMAGE_TAG}"
docker push "$ECR_REPO:latest"
echo "    Image pushed: $ECR_REPO:${IMAGE_TAG}"

# Step 3: Deploy security foundation
echo "==> Step 3: Security foundation stack..."
aws cloudformation deploy \
  --template-file aha_eventbridge_lambda/security-foundation.yaml \
  --stack-name phd-security-foundation \
  --capabilities CAPABILITY_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset

# Step 4: Deploy main stack (includes AgentCore Runtime + Lambda + everything)
echo "==> Step 4: Main stack (Lambda + AgentCore Runtime)..."

# Ensure deployment artifacts bucket exists
ARTIFACT_BUCKET="phd-deploy-artifacts-${AWS_ACCOUNT_ID}"
if ! aws s3api head-bucket --bucket "$ARTIFACT_BUCKET" --region "$REGION" 2>/dev/null; then
    echo "    Creating deployment artifacts bucket: $ARTIFACT_BUCKET..."
    if [ "$REGION" = "us-east-1" ]; then
        aws s3api create-bucket \
          --bucket "$ARTIFACT_BUCKET" \
          --region "$REGION" \
          --no-cli-pager >/dev/null
    else
        aws s3api create-bucket \
          --bucket "$ARTIFACT_BUCKET" \
          --region "$REGION" \
          --create-bucket-configuration LocationConstraint="$REGION" \
          --no-cli-pager >/dev/null
    fi
    aws s3api put-public-access-block \
      --bucket "$ARTIFACT_BUCKET" \
      --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
      --region "$REGION" --no-cli-pager >/dev/null
fi

# Package Lambda code (replaces sam build)
echo "    Packaging Lambda code..."
rm -rf .build
mkdir -p .build

# Lambda runtime provides boto3/botocore; only typing_extensions is needed externally
python3 -m pip install typing_extensions -t .build/ --quiet --disable-pip-version-check 2>/dev/null

# Copy application code (as subdirectories, exclude build caches and tests)
cp -r aha_eventbridge_lambda approval_lambda routing_config_lambda routing_approval_lambda phd_notification_classifier .build/
rm -rf .build/aha_eventbridge_lambda/.aws-sam .build/aha_eventbridge_lambda/tests .build/phd_notification_classifier/tests

# Copy template and rewrite CodeUri to point to .build/ (relative to template location)
sed 's|CodeUri: \.\./|CodeUri: ./|g' aha_eventbridge_lambda/template.yaml > .build/template.yaml

# Package template (uploads zip to S3, rewrites CodeUri)
echo "    Uploading to S3 and packaging template..."
aws cloudformation package \
  --template-file .build/template.yaml \
  --s3-bucket "$ARTIFACT_BUCKET" \
  --s3-prefix "lambda-packages" \
  --output-template-file .build/packaged.yaml \
  --region "$REGION" --no-cli-pager >/dev/null

# Build parameter overrides — only include non-empty values
PARAM_OVERRIDES="AgentContainerUri=$ECR_REPO:${IMAGE_TAG} RequireRoutingApproval=$REQUIRE_ROUTING_APPROVAL RemediationMode=$REMEDIATION_MODE BedrockModelId=$BEDROCK_MODEL_ID"
[ -n "$SES_SENDER" ] && PARAM_OVERRIDES="$PARAM_OVERRIDES SesIdentityArn=$SES_SENDER"
[ -n "$NOTIFICATION_EMAIL" ] && PARAM_OVERRIDES="$PARAM_OVERRIDES NotificationRecipientEmail=$NOTIFICATION_EMAIL"
[ -n "$JIRA_BASE_URL" ] && PARAM_OVERRIDES="$PARAM_OVERRIDES JiraBaseUrl=$JIRA_BASE_URL"
[ -n "$JIRA_PROJECT_KEY" ] && PARAM_OVERRIDES="$PARAM_OVERRIDES JiraProjectKey=$JIRA_PROJECT_KEY"
[ -n "$JIRA_ISSUE_TYPE" ] && PARAM_OVERRIDES="$PARAM_OVERRIDES JiraIssueType=$JIRA_ISSUE_TYPE"
[ -n "$JIRA_USER_EMAIL" ] && PARAM_OVERRIDES="$PARAM_OVERRIDES JiraUserEmail=$JIRA_USER_EMAIL"
[ -n "$JIRA_SECRET_ARN" ] && PARAM_OVERRIDES="$PARAM_OVERRIDES JiraSecretArn=$JIRA_SECRET_ARN"
[ -n "$SLACK_WEBHOOK_URL" ] && PARAM_OVERRIDES="$PARAM_OVERRIDES SlackWebhookUrl=$SLACK_WEBHOOK_URL"
[ -n "$AHA_EVENT_BUS" ] && PARAM_OVERRIDES="$PARAM_OVERRIDES AhaEventBusName=$AHA_EVENT_BUS"

# Deploy via CloudFormation (no SAM CLI needed)
echo "    Deploying CloudFormation stack..."
aws cloudformation deploy \
  --template-file .build/packaged.yaml \
  --stack-name "$STACK_NAME" \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --region "$REGION" \
  --no-fail-on-empty-changeset \
  --parameter-overrides $PARAM_OVERRIDES

# Cleanup build artifacts
rm -rf .build

echo ""
echo "============================================"
echo "  Deployment complete!"
echo "============================================"
echo "  Region:     $REGION"
echo "  Account:    $AWS_ACCOUNT_ID"
echo "  Stack:      $STACK_NAME"
echo "  Image:      $ECR_REPO:${IMAGE_TAG}"
echo ""
echo "  Test with:"
echo "    aws lambda invoke \\"
echo "      --function-name \$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \\"
echo "        --query 'Stacks[0].Outputs[?OutputKey==\`HealthEventFunctionArn\`].OutputValue' --output text) \\"
echo "      --payload fileb://test_health_event.json \\"
echo "      --region $REGION --cli-read-timeout 900 /tmp/test_response.json"
echo ""
echo "    cat /tmp/test_response.json"
echo ""
