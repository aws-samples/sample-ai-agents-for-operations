#!/usr/bin/env bash
#
# One-command deploy for the AKC Responder - Access Key Compromise Response
# pipeline. Wraps `sam build` + `sam deploy`.
#
# Usage:
#   ./deploy.sh                # interactive: prompts for all parameters
#   NOTIFICATION_EMAIL=you@example.com \
#   DEVOPS_AGENT_WEBHOOK_URL='https://...' \
#   WEBHOOK_SECRET='...' \
#   ./deploy.sh --non-interactive
#
# Requirements: AWS SAM CLI, AWS CLI v2, credentials configured for the target
# account/Region (e.g. `aws configure` or a named profile via AWS_PROFILE).
#
set -euo pipefail

STACK_NAME="${STACK_NAME:-AKCResponder}"
REGION="${AWS_REGION:-us-east-1}"

echo "==> Building (packaging Lambda code from pipeline/lambda/*)"
sam build

if [[ "${1:-}" == "--non-interactive" ]]; then
  : "${NOTIFICATION_EMAIL:?set NOTIFICATION_EMAIL}"
  : "${DEVOPS_AGENT_WEBHOOK_URL:?set DEVOPS_AGENT_WEBHOOK_URL}"
  : "${WEBHOOK_SECRET:?set WEBHOOK_SECRET}"
  echo "==> Deploying stack '$STACK_NAME' in '$REGION' (non-interactive)"
  sam deploy \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --capabilities CAPABILITY_IAM \
    --no-confirm-changeset \
    --resolve-s3 \
    --parameter-overrides \
      "NotificationEmail=${NOTIFICATION_EMAIL}" \
      "DevOpsAgentWebhookUrl=${DEVOPS_AGENT_WEBHOOK_URL}" \
      "WebhookSecret=${WEBHOOK_SECRET}" \
      "SlackWebhookUrl=${SLACK_WEBHOOK_URL:-}" \
      "MsTeamsWebhookUrl=${MS_TEAMS_WEBHOOK_URL:-}"
else
  echo "==> Deploying stack '$STACK_NAME' (guided)"
  sam deploy --guided --stack-name "$STACK_NAME" --region "$REGION" --capabilities CAPABILITY_IAM
fi

echo
echo "==> Done. Next steps:"
echo "    1. Confirm the SNS subscription email that was just sent to you."
echo "    2. Make sure the skill is uploaded and Active in your DevOps Agent Space."
echo "    3. Test:  aws lambda invoke --function-name AKCResponder-EventRouter \\"
echo "                --payload fileb://events/test-health-exposed-key.json \\"
echo "                --cli-binary-format raw-in-base64-out /tmp/out.json && cat /tmp/out.json"
