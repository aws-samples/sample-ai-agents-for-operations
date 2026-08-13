# Slack Integration Lambda

This directory contains the Lambda function handler and layer for Slack integration.

## Structure

- `handler.py` - Lambda function handler for Slack events
- `layer/` - Lambda layer directory
  - `requirements.txt` - Python dependencies for the Lambda layer (slack-bolt, slack-sdk)

## Lambda Layer

The Lambda layer is built automatically by CDK during synthesis. The `SlackIntegrationConstruct` uses `BundlingOptions` to pip install dependencies from `layer/requirements.txt`. No manual build step is needed.

## Dependencies

- slack-bolt>=1.18.0 - Slack Bolt framework for Python
- slack-sdk>=3.23.0 - Slack SDK for Python

## Usage in CDK

The SlackIntegrationConstruct uses this layer. During CDK asset bundling, the `python-packages/` directory will be packaged as the Lambda layer:

```python
layer = lambda_.LayerVersion(
    self, "SlackDependenciesLayer",
    code=lambda_.Code.from_asset("lambda/slack_integration_stack/slack_integration/layer"),
    compatible_runtimes=[lambda_.Runtime.PYTHON_3_11],
    description="Slack Bolt and SDK dependencies"
)
```
