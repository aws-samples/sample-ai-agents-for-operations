"""Slack integration handler construct.

This module provides the SlackIntegrationConstruct for creating a Slack bot
integration that connects Slack events to the AgentCore runtime.

The construct creates:
- Lambda function with Slack Bolt SDK handler
- Lambda layer with slack-bolt and slack-sdk dependencies
- API Gateway route at /slack/events for Slack event subscriptions
- Secrets Manager secret for Slack credentials (populate after deploy)
- IAM permissions for AgentCore invocation, self-invocation, and Secrets Manager read
"""
from aws_cdk import (
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    Duration,
)
import aws_cdk as cdk
from constructs import Construct

from .integration_handler_construct import (
    IntegrationHandlerConstruct,
)


import subprocess
import os
import hashlib
from pathlib import Path
import jsii

_INFRA_DIR = Path(__file__).resolve().parent.parent.parent
_SLACK_LAMBDA_DIR = str(_INFRA_DIR / "lambda" / "slack_integration")
_SLACK_LAYER_DIR = str(_INFRA_DIR / "lambda" / "slack_integration" / "layer")


@jsii.implements(cdk.ILocalBundling)
class PipLocalBundling:
    """Local bundling that runs pip install without Docker.
    
    Caches the installed packages in a .build/ directory next to requirements.txt.
    Only re-runs pip install when requirements.txt content changes.
    """

    def __init__(self, source_path: str) -> None:
        self._source_path = source_path
        self._build_dir = os.path.join(source_path, ".build")
        self._hash_file = os.path.join(self._build_dir, ".requirements_hash")

    def _requirements_hash(self) -> str:
        """Hash the requirements.txt content to detect changes."""
        req_path = os.path.join(self._source_path, "requirements.txt")
        with open(req_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def _is_cache_valid(self) -> bool:
        """Check if cached build matches current requirements.txt."""
        if not os.path.exists(self._hash_file):
            return False
        with open(self._hash_file, "r", encoding="utf-8") as f:
            return f.read().strip() == self._requirements_hash()

    def _write_hash(self) -> None:
        with open(self._hash_file, "w", encoding="utf-8") as f:
            f.write(self._requirements_hash())

    def try_bundle(self, output_dir: str, *, image, asset_hash=None, bundling_file_access=None,
                   command=None, entrypoint=None, environment=None, local=None,
                   network=None, output_type=None, platform=None,
                   security_opt=None, user=None, volumes=None,
                   volumes_from=None, working_directory=None) -> bool:
        """Attempt local pip install, using cache when possible.

        Uses a symlink from CDK's output directory to the cached build to avoid
        expensive file copies on every synthesis (important for test performance).
        """
        requirements = os.path.join(self._source_path, "requirements.txt")
        cached_python = os.path.join(self._build_dir, "python")

        try:
            # Only pip install if cache is stale
            if not self._is_cache_valid():
                import shutil
                if os.path.exists(self._build_dir):
                    shutil.rmtree(self._build_dir)
                os.makedirs(cached_python, exist_ok=True)
                subprocess.check_call(
                    ["pip", "install", "-r", requirements, "-t", cached_python, "-q"]
                )
                self._write_hash()

            # Symlink to avoid copying 6MB+ on every synthesis
            dest_python = os.path.join(output_dir, "python")
            if os.path.exists(dest_python) or os.path.islink(dest_python):
                if os.path.islink(dest_python):
                    os.unlink(dest_python)
                else:
                    import shutil
                    shutil.rmtree(dest_python)
            os.symlink(os.path.abspath(cached_python), dest_python)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


class SlackIntegrationConstruct(IntegrationHandlerConstruct):
    """
    Slack integration handler construct.

    This construct extends IntegrationHandlerConstruct to provide Slack-specific
    event handling for the Bedrock Quota Agent. It creates a Lambda function that:
    - Handles Slack events (app_mention, message, slash commands)
    - Derives session context from Slack thread_ts and user_id
    - Invokes AgentCore runtime with session context
    - Posts agent responses back to Slack channels/threads
    - Uses async self-invocation to meet Slack's 3-second timeout requirement

    The construct creates:
    - Lambda layer with slack-bolt and slack-sdk dependencies
    - Lambda function with Slack event handler code
    - API Gateway route at /slack/events
    - IAM permissions for AgentCore invocation, self-invocation, and Secrets Manager read
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        api: apigw.RestApi,
        runtime_arn: str,
        runtime_region: str,
        environment: str,
        secret_name: str,
        slash_command: str = "/bedrock",
        deploy_test_lambda: bool = False,
        dedup_table_name: str = None,
        reserved_concurrent_executions: int = 10,
        **kwargs
    ) -> None:
        """
        Initialize the Slack integration construct.

        Args:
            scope: Parent construct
            construct_id: Unique identifier for this construct
            api: API Gateway REST API to add routes to
            runtime_arn: ARN of the AgentCore runtime to invoke
            runtime_region: AWS region of the AgentCore runtime
            environment: Deployment environment (dev, staging, prod)
            secret_name: Name for the Secrets Manager secret created by this construct
                (will contain SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET after operator populates it)
            slash_command: Slack slash command name (default: "/bedrock")
            deploy_test_lambda: Whether to deploy test Lambda function (default: False)
            dedup_table_name: DynamoDB table name for event deduplication. Required —
                the handler refuses to start without it, so a stack deployed without
                a table would never serve traffic. Deduplication is a cost control:
                Slack retries webhooks on any non-2xx response, and without dedup each
                retry becomes another Amazon Bedrock invocation.
            reserved_concurrent_executions: Maximum concurrent executions for this
                Lambda (default: 10). Caps in-flight Amazon Bedrock invocations.
            **kwargs: Additional construct properties

        Raises:
            ValueError: If dedup_table_name is missing, or if
                reserved_concurrent_executions is not a positive integer
        """
        if not dedup_table_name:
            raise ValueError(
                "dedup_table_name is required. Event deduplication is a cost control "
                "for request flooding — the Lambda handler refuses to start without "
                "DEDUP_TABLE_NAME, so omitting it would deploy a stack that cannot "
                "serve traffic."
            )

        if (
            not isinstance(reserved_concurrent_executions, int)
            or isinstance(reserved_concurrent_executions, bool)
            or reserved_concurrent_executions <= 0
        ):
            raise ValueError(
                "reserved_concurrent_executions must be a positive integer, "
                f"got: {reserved_concurrent_executions!r}"
            )

        self.secret_name = secret_name
        self.slash_command = slash_command
        self.deploy_test_lambda = deploy_test_lambda
        self.dedup_table_name = dedup_table_name
        self.reserved_concurrent_executions = reserved_concurrent_executions

        super().__init__(
            scope,
            construct_id,
            api=api,
            runtime_arn=runtime_arn,
            runtime_region=runtime_region,
            environment=environment,
            route_path="/slack/events",
            **kwargs
        )

        # Create the Secrets Manager secret with placeholder values.
        # Operator must populate with real credentials after deploy.
        self.secret = secretsmanager.Secret(
            self, "SlackCredentials",
            secret_name=self.secret_name,
            description=(
                f"Slack credentials for Bedrock Quota Agent ({self.environment}). "
                f"Populate with SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET after deploy."
            ),
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"SLACK_BOT_TOKEN":"","SLACK_SIGNING_SECRET":""}',
                generate_string_key="__init_placeholder__",
            ),
            removal_policy=cdk.RemovalPolicy.RETAIN if self.environment == "prod" else cdk.RemovalPolicy.DESTROY,
        )

        # Create Lambda layer with Slack dependencies
        self._create_lambda_layer()

        # Create Lambda function (sets self.lambda_function)
        self.lambda_function = self._create_lambda_function()

        # Grant base permissions (AgentCore invocation, CloudWatch Logs)
        self._grant_agentcore_permissions()

        # Grant Secrets Manager read permission
        self.secret.grant_read(self.lambda_function)

        # Grant self-invocation permission for async pattern
        self._grant_self_invocation_permission()

        # Create API Gateway route
        self._create_api_route()

        # Optionally create test Lambda function
        if self.deploy_test_lambda:
            self.test_lambda_function = self._create_test_lambda_function()
            self._grant_test_lambda_permissions()
            self._create_test_api_route()
    
    def _create_lambda_layer(self) -> None:
        """Create Lambda layer with slack-bolt and slack-sdk dependencies.
        
        Uses CDK's built-in bundling to pip install dependencies during synthesis.
        Tries local pip install first (fast, no Docker needed), falls back to
        Docker container bundling if local bundling fails.
        
        Note: Lambda LayerVersion resources don't support tags in CloudFormation,
        so tags cannot be applied to the layer.
        """
        layer_path = _SLACK_LAYER_DIR

        self.lambda_layer = lambda_.LayerVersion(
            self,
            "SlackDependenciesLayer",
            code=lambda_.Code.from_asset(
                layer_path,
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_11.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install -r requirements.txt -t /asset-output/python"
                    ],
                    local=PipLocalBundling(layer_path),
                ),
            ),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_11],
            description="Slack Bolt SDK and Slack SDK dependencies",
        )
    
    def _create_lambda_function(self) -> lambda_.Function:
        """
        Create Lambda function with Slack event handler code.
        
        Returns:
            The created Lambda function
        """
        env_vars = {
            "SLACK_SECRET_ARN": self.secret.secret_arn,
            "SLACK_SLASH_COMMAND": self.slash_command,
            "AGENTCORE_ARN": self.runtime_arn,
            "AGENTCORE_REGION": self.runtime_region,
            "ENVIRONMENT": self.environment,
            "DEDUP_TABLE_NAME": self.dedup_table_name,
        }

        function = lambda_.Function(
            self,
            "SlackHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_SLACK_LAMBDA_DIR),
            layers=[self.lambda_layer],
            timeout=Duration.seconds(30),
            environment=env_vars,
            # Caps the number of Amazon Bedrock invocations that can be in flight,
            # bounding cost if the endpoint is flooded.
            #
            # IMPORTANT: this pool is shared by two paths. The synchronous webhook
            # invocation acknowledges Slack within its 3-second budget, then
            # self-invokes asynchronously to do the actual agent work. Both draw
            # from this same reservation. Set it too low and async invocations get
            # throttled — Lambda retries an async invocation twice, then drops it,
            # which surfaces as a Slack message that never receives a reply.
            # Raise this together with throttling_rate_limit, not on its own.
            reserved_concurrent_executions=self.reserved_concurrent_executions,
            description=f"Slack integration handler for {self.environment} environment",
        )

        # Grant DynamoDB permissions for event dedup
        function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:PutItem"],
                resources=[
                    f"arn:aws:dynamodb:*:*:table/{self.dedup_table_name}",
                ],
            )
        )

        return function
    
    def _grant_self_invocation_permission(self) -> None:
        """Grant Lambda permission to invoke itself for async processing.
        
        Note: We use a wildcard resource to avoid circular dependency issues.
        This grants permission to invoke any Lambda function, which is acceptable
        since the Lambda execution role is scoped to this specific function.
        """
        self.lambda_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=["*"],  # Wildcard to avoid circular dependency
            )
        )
    
    def _create_api_route(self) -> None:
        """Create API Gateway route at /slack/events."""
        # Create /slack resource
        slack_resource = self.api.root.add_resource("slack")
        
        # Create /slack/events resource
        events_resource = slack_resource.add_resource("events")
        
        # Add POST method with Lambda proxy integration
        events_resource.add_method(
            "POST",
            apigw.LambdaIntegration(self.lambda_function),
        )
        
        # Store the full events URL as a public attribute
        self.events_url = f"{self.api.url}slack/events"
    
    def _create_test_lambda_function(self) -> lambda_.Function:
        """
        Create test Lambda function with mock Slack client.
        
        Returns:
            The created test Lambda function
        """
        function = lambda_.Function(
            self,
            "SlackTestHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="test_handler.lambda_handler",
            code=lambda_.Code.from_asset(_SLACK_LAMBDA_DIR),
            layers=[self.lambda_layer],  # Attach the Slack dependencies layer
            timeout=Duration.seconds(30),
            environment={
                "SLACK_SECRET_ARN": self.secret.secret_arn,
                "SLACK_SLASH_COMMAND": self.slash_command,
                "AGENTCORE_ARN": self.runtime_arn,
                "AGENTCORE_REGION": self.runtime_region,
                "ENVIRONMENT": self.environment,
                # Mirrors the main handler. Kept in step deliberately: the test
                # handler exercises the same code paths, and the real handler
                # refuses to start without this variable.
                "DEDUP_TABLE_NAME": self.dedup_table_name,
            },
            # This handler reaches Amazon Bedrock through AgentCore just like the
            # real one and is exposed on its own API Gateway route, so it needs its
            # own cost ceiling. Kept deliberately low: it exists for integration
            # testing in dev/test only and is never deployed to prod.
            reserved_concurrent_executions=2,
            description=f"Slack integration test handler for {self.environment} environment",
        )

        return function
    
    def _grant_test_lambda_permissions(self) -> None:
        """Grant test Lambda permissions for AgentCore invocation and event dedup."""
        # Grant AgentCore invocation permission
        # Need both base ARN and wildcard pattern to cover all endpoints
        self.test_lambda_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[
                    self.runtime_arn,
                    f"{self.runtime_arn}/*"
                ],
            )
        )

        # Dedup writes, matching the main handler. Without this the test handler
        # would receive AccessDenied on the dedup put, which now fails closed and
        # would silently drop every event it was given.
        self.test_lambda_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:PutItem"],
                resources=[
                    f"arn:aws:dynamodb:*:*:table/{self.dedup_table_name}",
                ],
            )
        )
    
    def _create_test_api_route(self) -> None:
        """Create API Gateway route at /slack/events/test for test Lambda."""
        # Get /slack/events resource (already created by _create_api_route)
        slack_resource = self.api.root.get_resource("slack")
        events_resource = slack_resource.get_resource("events")
        
        # Create /slack/events/test resource
        test_resource = events_resource.add_resource("test")
        
        # Add POST method with Lambda proxy integration
        test_resource.add_method(
            "POST",
            apigw.LambdaIntegration(self.test_lambda_function),
        )
        
        # Store the full test events URL as a public attribute
        self.test_events_url = f"{self.api.url}slack/events/test"
