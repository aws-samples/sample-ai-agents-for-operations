"""Slack integration stack for Bedrock Quota Agent.

This module provides the SlackIntegrationStack for connecting the AgentCore
runtime to Slack via API Gateway and Lambda. Deploy this stack to enable
Slack bot interaction; skip it if Slack integration is not needed.

After deployment, populate the Slack credentials secret:
    make set-slack-creds ENV={environment}

That target prompts without echoing and passes the values via a mode-0600
temporary file. Do not pass credentials as an inline --secret-string argument:
that records the token in shell history and exposes it in the process table.

The stack creates:
- API Gateway REST API with /slack/events route
- Lambda function with Slack Bolt SDK handler
- Secrets Manager secret for Slack credentials (empty until operator populates)
- IAM permissions to read the Slack credentials secret
- CloudFormation outputs for endpoint URLs and secret ARN
"""

from aws_cdk import Annotations, Stack, CfnOutput, Tags
from aws_cdk import aws_apigateway as apigw
from constructs import Construct

from custom_constructs.slack_integration_stack.slack_integration_construct import (
    SlackIntegrationConstruct,
)


class SlackIntegrationStack(Stack):
    """
    Slack integration stack for Bedrock Quota Agent.

    Connects Slack to the AgentCore runtime via API Gateway + Lambda.
    Creates a Secrets Manager secret that must be populated with Slack
    credentials after deployment.

    If Slack integration is not needed, simply do not deploy this stack.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: str,
        runtime_arn: str,
        dedup_table_name: str = None,
        slack_secret_name: str = None,
        slack_slash_command: str = "/bedrock",
        api_auth_type: str = "NONE",
        throttling_rate_limit: int = 100,
        throttling_burst_limit: int = 50,
        reserved_concurrent_executions: int = 10,
        **kwargs
    ) -> None:
        """
        Initialize the Slack integration stack.

        Args:
            scope: CDK app or parent construct
            construct_id: Unique identifier for this stack
            environment: Deployment environment (dev, staging, prod)
            runtime_arn: ARN of the AgentCore runtime to invoke
            dedup_table_name: Optional DynamoDB table name for event deduplication
            slack_secret_name: Name for the Secrets Manager secret that will hold
                Slack credentials. Defaults to
                "bedrock-quota-agent/{environment}/slack-credentials".
            slack_slash_command: Slack slash command name (default: "/bedrock")
            api_auth_type: Authentication type for API Gateway (NONE, API_KEY, IAM, COGNITO)
                          Note: Currently only NONE is implemented.
            throttling_rate_limit: API Gateway steady-state request rate limit in
                requests per second (default: 100). This is a cost-control boundary,
                not just a performance setting — every request that reaches the
                Lambda can trigger an Amazon Bedrock model invocation. The default
                is sized for a small Slack workspace; raise it deliberately after
                measuring real usage.
            throttling_burst_limit: API Gateway burst capacity in requests
                (default: 50). Absorbs short spikes such as Slack webhook retries.
            reserved_concurrent_executions: Maximum concurrent executions for the
                Slack Lambda (default: 10). Caps the number of in-flight Amazon
                Bedrock invocations. See the note in SlackIntegrationConstruct:
                this pool is shared by the synchronous webhook path and the
                asynchronous self-invoked processing path.
            **kwargs: Additional stack properties

        Raises:
            ValueError: If environment parameter is not a non-empty string, or if
                any throttling or concurrency limit is not a positive integer
        """
        super().__init__(scope, construct_id, **kwargs)

        if not environment or not isinstance(environment, str):
            raise ValueError("environment parameter must be a non-empty string")

        allowed_environments = ["dev", "staging", "prod"]
        if environment not in allowed_environments:
            raise ValueError(
                f"environment must be one of {allowed_environments}, got: {environment}"
            )

        allowed_auth_types = ["NONE", "API_KEY", "IAM", "COGNITO"]
        if api_auth_type not in allowed_auth_types:
            raise ValueError(
                f"api_auth_type must be one of {allowed_auth_types}, got: {api_auth_type}"
            )

        if api_auth_type != "NONE":
            import warnings
            warnings.warn(
                f"api_auth_type '{api_auth_type}' is not yet implemented. "
                f"Only 'NONE' is currently supported.",
                FutureWarning
            )

        # These are cost-control boundaries for T7 (request flooding exhausting
        # Bedrock quotas). A non-positive or non-integer value would silently
        # disable the limit, so reject it at synth time rather than deploy an
        # unbounded stack.
        for name, value in (
            ("throttling_rate_limit", throttling_rate_limit),
            ("throttling_burst_limit", throttling_burst_limit),
            ("reserved_concurrent_executions", reserved_concurrent_executions),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer, got: {value!r}")

        self.env_name = environment
        self.runtime_arn = runtime_arn
        self.throttling_rate_limit = throttling_rate_limit
        self.throttling_burst_limit = throttling_burst_limit
        self.reserved_concurrent_executions = reserved_concurrent_executions
        self.slack_secret_name = (
            slack_secret_name or f"bedrock-quota-agent/{environment}/slack-credentials"
        )
        self.slack_slash_command = slack_slash_command
        self.api_auth_type = api_auth_type
        self.dedup_table_name = dedup_table_name

        self.runtime_region = self._extract_region_from_arn(runtime_arn)

        self._create_api_gateway()
        self._create_slack_integration()
        self._create_outputs()
        self._apply_tags()

        if self.env_name == "prod":
            Annotations.of(self).add_warning(
                "Slack credentials secret was created with empty placeholder values. "
                "Remember to populate it after deployment:\n"
                f"  make set-slack-creds ENV={self.env_name}\n"
                f"  (secret id: {self.slack_secret_name})\n"
                "Do not pass credentials as an inline --secret-string argument: that "
                "records the token in shell history and exposes it in the process table."
            )

    def _extract_region_from_arn(self, arn: str) -> str:
        """Extract AWS region from AgentCore runtime ARN."""
        from aws_cdk import Fn, Token

        if Token.is_unresolved(arn):
            return Fn.select(3, Fn.split(":", arn))

        try:
            parts = arn.split(":")
            if len(parts) >= 4:
                return parts[3]
            raise ValueError(f"Invalid ARN format: {arn}")
        except Exception as e:
            raise ValueError(f"Failed to extract region from ARN {arn}: {e}")

    def _create_api_gateway(self) -> None:
        """Create API Gateway REST API resource with throttling and tracing."""
        self.api_gateway = apigw.RestApi(
            self,
            f"ApiGateway-{self.env_name}",
            rest_api_name=f"bedrock-quota-agent-{self.env_name}",
            description=f"API Gateway for Bedrock Quota Agent ({self.env_name} environment)",
            deploy_options=apigw.StageOptions(
                stage_name=self.env_name,
                throttling_rate_limit=self.throttling_rate_limit,
                throttling_burst_limit=self.throttling_burst_limit,
                metrics_enabled=True,
                tracing_enabled=True,
            ),
        )

        self.api_endpoint_url = self.api_gateway.url

    def _create_slack_integration(self) -> None:
        """Instantiate SlackIntegrationConstruct with managed secret."""
        deploy_test = self.env_name in ["dev", "test"]

        self.slack_integration = SlackIntegrationConstruct(
            self,
            f"SlackIntegration-{self.env_name}",
            api=self.api_gateway,
            runtime_arn=self.runtime_arn,
            runtime_region=self.runtime_region,
            environment=self.env_name,
            secret_name=self.slack_secret_name,
            slash_command=self.slack_slash_command,
            deploy_test_lambda=deploy_test,
            dedup_table_name=self.dedup_table_name,
            reserved_concurrent_executions=self.reserved_concurrent_executions,
        )

    def _create_outputs(self) -> None:
        """Create CloudFormation outputs with export names."""
        CfnOutput(
            self,
            "ApiEndpointUrl",
            value=self.api_endpoint_url,
            description="Base URL of the API Gateway",
            export_name=f"{self.stack_name}-ApiEndpointUrl",
        )

        CfnOutput(
            self,
            "SlackEventsUrl",
            value=self.slack_integration.events_url,
            description="URL for Slack event subscriptions",
            export_name=f"{self.stack_name}-SlackEventsUrl",
        )

        CfnOutput(
            self,
            "SlackSecretArn",
            value=self.slack_integration.secret.secret_arn,
            description="ARN of the Slack credentials secret (populate after deploy)",
            export_name=f"{self.stack_name}-SlackSecretArn",
        )

        if hasattr(self.slack_integration, 'test_events_url'):
            CfnOutput(
                self,
                "SlackTestEventsUrl",
                value=self.slack_integration.test_events_url,
                description="URL for Slack integration testing",
                export_name=f"{self.stack_name}-SlackTestEventsUrl",
            )

    def _apply_tags(self) -> None:
        """Apply tags to all resources in the stack."""
        Tags.of(self).add("Project", "BedrockQuotaAgent")
        Tags.of(self).add("Environment", self.env_name)
        Tags.of(self).add("agent-managed", "true")
