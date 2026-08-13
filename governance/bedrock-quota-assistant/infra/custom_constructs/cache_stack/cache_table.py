"""DynamoDB table construct for quota code cache."""

from aws_cdk import RemovalPolicy, aws_dynamodb as dynamodb
from constructs import Construct


class CacheTable(Construct):
    """Create DynamoDB table for caching quota codes."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        table_name: str,
        removal_policy: RemovalPolicy = RemovalPolicy.RETAIN,
    ) -> None:
        """
        Create DynamoDB table for quota code cache.

        Args:
            scope: Parent construct
            construct_id: Unique identifier
            table_name: DynamoDB table name (environment-specific)
            removal_policy: What to do with the table when the stack is deleted
                           (default: RETAIN for production safety)
        """
        super().__init__(scope, construct_id)

        self.table = dynamodb.Table(
            self,
            "Table",
            table_name=table_name,
            partition_key=dynamodb.Attribute(
                name="PK",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="SK",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=removal_policy,
        )
