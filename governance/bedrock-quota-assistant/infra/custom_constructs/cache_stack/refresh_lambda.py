"""Lambda function construct for refreshing quota code cache."""

from pathlib import Path

from aws_cdk import Duration, aws_events as events, aws_events_targets as targets, aws_iam as iam, aws_lambda as lambda_
from constructs import Construct

_INFRA_DIR = Path(__file__).resolve().parent.parent.parent
_CACHE_REFRESH_DIR = str(_INFRA_DIR / "lambda" / "cache_refresh")


class RefreshLambda(Construct):
    """Create Lambda function with EventBridge schedule for cache refresh."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        table,
        refresh_schedule: str = "rate(7 days)",
    ) -> None:
        """
        Create Lambda function for quota code cache refresh.

        Args:
            scope: Parent construct
            construct_id: Unique identifier
            table: DynamoDB table to write to
            refresh_schedule: EventBridge schedule expression (default: rate(7 days))
        """
        super().__init__(scope, construct_id)

        # Create Lambda function
        self._function = lambda_.Function(
            self,
            "Function",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(_CACHE_REFRESH_DIR),
            timeout=Duration.seconds(300),
            memory_size=256,
            architecture=lambda_.Architecture.ARM_64,
            environment={
                "CACHE_TABLE_NAME": table.table_name,
                "REFRESH_REGION": "us-east-1",
                "INFERENCE_PROFILE_REGIONS": "us-east-1,us-west-2,eu-west-1,ap-southeast-1,ap-northeast-1",
            },
        )

        # Grant Service Quotas permissions
        self._function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["servicequotas:ListServiceQuotas",
                         "servicequotas:GetServiceQuota"],
                resources=["*"],
            )
        )

        # Grant DynamoDB write + query permissions (query needed for quota code lookup)
        self._function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:BatchWriteItem",
                    "dynamodb:Query",
                ],
                resources=[table.table_arn],
            )
        )

        # Grant CloudWatch ListMetrics + GetMetricStatistics for model discovery
        self._function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "cloudwatch:ListMetrics",
                    "cloudwatch:GetMetricStatistics",
                ],
                resources=["*"],
            )
        )

        # Grant Bedrock permissions for inference profile discovery
        self._function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:ListInferenceProfiles",
                    "bedrock:ListTagsForResource",
                ],
                resources=["*"],
            )
        )

        # Create EventBridge rule with schedule
        rule = events.Rule(
            self,
            "ScheduleRule",
            schedule=events.Schedule.expression(refresh_schedule),
            enabled=True,
        )

        # Add Lambda as target
        rule.add_target(targets.LambdaFunction(self._function))

    @property
    def function(self) -> lambda_.Function:
        """The Lambda function resource."""
        return self._function
