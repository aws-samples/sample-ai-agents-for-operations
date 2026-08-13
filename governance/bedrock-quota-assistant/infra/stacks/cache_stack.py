"""Cache stack for quota code caching infrastructure."""

from aws_cdk import CfnOutput, RemovalPolicy, Stack, Tags
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct

from custom_constructs.cache_stack.async_cache_populator import AsyncCachePopulator
from custom_constructs.cache_stack.cache_table import CacheTable
from custom_constructs.cache_stack.refresh_lambda import RefreshLambda


class CacheStack(Stack):
    """Cache stack containing DynamoDB table, Lambda function, and EventBridge schedule."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: str,
        refresh_schedule: str = "rate(7 days)",
        **kwargs
    ) -> None:
        """
        Initialize the Cache stack.

        Args:
            scope: CDK app or parent construct
            construct_id: Unique identifier for this stack
            environment: Deployment environment (dev, staging, prod)
            refresh_schedule: EventBridge schedule expression (default: rate(7 days))
            **kwargs: Additional stack properties
        """
        super().__init__(scope, construct_id, **kwargs)

        # Validate environment parameter
        if not environment or not isinstance(environment, str):
            raise ValueError("environment parameter must be a non-empty string")

        self.env_name = environment

        # Create resources in dependency order
        self._create_cache_table()
        self._create_refresh_lambda(refresh_schedule)
        self._create_async_cache_populator()
        self._create_outputs()
        self._apply_tags()

    def _create_cache_table(self) -> None:
        """Create DynamoDB table for quota code cache."""
        table_name = f"bedrock-quota-codes-{self.env_name}"
        
        # Use DESTROY removal policy by default to allow easy cleanup
        # Override to RETAIN for production environments if needed
        removal_policy = RemovalPolicy.DESTROY
        
        self._cache_table = CacheTable(
            self,
            "CacheTable",
            table_name=table_name,
            removal_policy=removal_policy,
        )

    def _create_refresh_lambda(self, refresh_schedule: str) -> None:
        """Create Lambda function with EventBridge schedule."""
        self.refresh_lambda = RefreshLambda(
            self,
            "RefreshLambda",
            table=self._cache_table.table,
            refresh_schedule=refresh_schedule,
        )

    def _create_async_cache_populator(self) -> None:
        """Create custom resource for async cache population."""
        self.async_populator = AsyncCachePopulator(
            self,
            "AsyncCachePopulator",
            function=self.refresh_lambda.function,
        )

    def _create_outputs(self) -> None:
        """Create CloudFormation outputs."""
        CfnOutput(
            self,
            "TableName",
            value=self._cache_table.table.table_name,
            description="Name of the cache DynamoDB table",
            export_name=f"{self.stack_name}-TableName",
        )
        
        CfnOutput(
            self,
            "TableArn",
            value=self._cache_table.table.table_arn,
            description="ARN of the cache DynamoDB table",
            export_name=f"{self.stack_name}-TableArn",
        )
        
        CfnOutput(
            self,
            "LambdaFunctionArn",
            value=self.refresh_lambda.function.function_arn,
            description="ARN of the refresh Lambda function",
            export_name=f"{self.stack_name}-LambdaFunctionArn",
        )

    def _apply_tags(self) -> None:
        """Apply tags to all resources in the stack."""
        Tags.of(self).add("Project", "BedrockQuotaAgent")
        Tags.of(self).add("Environment", self.env_name)
        Tags.of(self).add("agent-managed", "true")

    @property
    def table(self) -> dynamodb.Table:
        """DynamoDB table for cross-stack references."""
        return self._cache_table.table

    @property
    def table_name(self) -> str:
        """Table name for convenience."""
        return self._cache_table.table.table_name

    @property
    def table_arn(self) -> str:
        """Table ARN for convenience."""
        return self._cache_table.table.table_arn
