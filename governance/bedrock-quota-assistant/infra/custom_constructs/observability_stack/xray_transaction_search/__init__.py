"""X-Ray Transaction Search construct for ObservabilityStack.

Uses a custom resource Lambda to manage Transaction Search configuration,
avoiding AlreadyExists errors from the native AWS::XRay::TransactionSearchConfig
resource (which is an account-level singleton).

The custom resource:
- On Create: checks if Transaction Search is already enabled, enables if not,
  and sets the desired indexing percentage.
- On Update: updates the indexing percentage via xray:UpdateIndexingRule.
- On Delete: either retains (no-op) or disables Transaction Search based on
  the retain_on_delete parameter.
"""

import json
from pathlib import Path

from aws_cdk import CustomResource, Duration, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import custom_resources as cr
from constructs import Construct


class XRayTransactionSearchConstruct(Construct):
    """Enable X-Ray Transaction Search using a custom resource.

    Uses a Lambda-backed custom resource instead of the native
    AWS::XRay::TransactionSearchConfig to handle the account-level
    singleton nature of Transaction Search idempotently.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        indexing_percentage: int = 1,
        retain_on_delete: bool = True,
    ) -> None:
        """
        Create X-Ray Transaction Search configuration.

        Args:
            scope: Parent construct
            construct_id: Unique identifier
            indexing_percentage: Percentage of spans to index (0-100, default 1)
            retain_on_delete: If True, leave Transaction Search enabled on stack
                delete. If False, disable it by reverting destination to X-Ray.
                Defaults to True for safety.
        """
        super().__init__(scope, construct_id)

        stack = Stack.of(self)
        region = stack.region
        account = stack.account
        partition = stack.partition

        # Create CloudWatch Logs resource policy to allow X-Ray to write logs
        self.logs_resource_policy = logs.CfnResourcePolicy(
            self,
            "TransactionSearchAccess",
            policy_name="TransactionSearchAccess",
            policy_document=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "TransactionSearchXRayAccess",
                        "Effect": "Allow",
                        "Principal": {
                            "Service": "xray.amazonaws.com"
                        },
                        "Action": "logs:PutLogEvents",
                        "Resource": [
                            f"arn:{partition}:logs:{region}:{account}:log-group:aws/spans:*",
                            f"arn:{partition}:logs:{region}:{account}:log-group:/aws/application-signals/data:*",
                        ],
                        "Condition": {
                            "ArnLike": {
                                "aws:SourceArn": f"arn:{partition}:xray:{region}:{account}:*"
                            },
                            "StringEquals": {
                                "aws:SourceAccount": account
                            }
                        }
                    }
                ]
            }),
        )

        # Create custom resource provider
        provider = self._create_provider()

        # Create custom resource for Transaction Search management
        self.transaction_search_config = CustomResource(
            self,
            "TransactionSearchConfig",
            service_token=provider.service_token,
            properties={
                "IndexingPercentage": indexing_percentage,
                "RetainOnDelete": str(retain_on_delete).lower(),
            },
        )

        # Ensure resource policy exists before enabling Transaction Search
        self.transaction_search_config.node.add_dependency(
            self.logs_resource_policy
        )

    def _create_provider(self) -> cr.Provider:
        """Create custom resource provider for X-Ray Transaction Search."""
        lambda_role = iam.Role(
            self,
            "TransactionSearchProviderRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "xray:GetTraceSegmentDestination",
                    "xray:UpdateTraceSegmentDestination",
                    "xray:GetIndexingRules",
                    "xray:UpdateIndexingRule",
                    "application-signals:StartDiscovery",
                ],
                resources=["*"],
            )
        )

        handler_dir = Path(__file__).parent

        on_event_handler = lambda_.Function(
            self,
            "TransactionSearchHandler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.on_event",
            code=lambda_.Code.from_asset(str(handler_dir)),
            timeout=Duration.minutes(14),
            role=lambda_role,
        )

        return cr.Provider(
            self,
            "TransactionSearchProvider",
            on_event_handler=on_event_handler,
        )
