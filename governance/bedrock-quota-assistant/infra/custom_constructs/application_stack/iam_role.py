"""IAM role construct for Bedrock Quota Agent."""

from aws_cdk import aws_iam as iam
from constructs import Construct


class AgentIamRole(Construct):
    """Create IAM role for the Bedrock Quota Agent with least-privilege permissions."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        memory_resource_arn: str,
        parameter_namespace: str = "/bedrock-quota-agent/",
        cache_table_arn: str = None,
        ecr_repository_arn: str = None,
    ) -> None:
        """
        Create IAM role for the Bedrock Quota Agent.

        Args:
            scope: Parent construct
            construct_id: Unique identifier
            memory_resource_arn: ARN of the memory resource for scoped permissions
            parameter_namespace: SSM parameter namespace for scoped access
            cache_table_arn: Optional ARN of the cache table for DynamoDB permissions
            ecr_repository_arn: Optional ARN of the ECR repository for scoped image pull
        """
        super().__init__(scope, construct_id)

        # Get account and region from the stack
        from aws_cdk import Stack, Aws
        stack = Stack.of(self)
        account = Aws.ACCOUNT_ID
        region = Aws.REGION

        # Create role with trust policy for Bedrock services with conditions
        # Allow both bedrock.amazonaws.com and bedrock-agentcore.amazonaws.com
        self.role = iam.Role(
            self,
            "AgentRole",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal(
                    "bedrock.amazonaws.com",
                    conditions={
                        "StringEquals": {
                            "aws:SourceAccount": account
                        },
                        "ArnLike": {
                            "aws:SourceArn": f"arn:aws:bedrock:{region}:{account}:*"
                        }
                    }
                ),
                iam.ServicePrincipal(
                    "bedrock-agentcore.amazonaws.com",
                    conditions={
                        "StringEquals": {
                            "aws:SourceAccount": account
                        },
                        "ArnLike": {
                            "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"
                        }
                    }
                ),
            ),
            description="IAM role for Bedrock Quota Agent",
        )

        # Add all required permissions
        self._add_ecr_permissions(ecr_repository_arn)
        self._add_cloudwatch_logs_permissions()
        self._add_xray_permissions()
        self._add_cloudwatch_metrics_permissions()
        self._add_service_quotas_permissions()
        self._add_cloudwatch_permissions()
        self._add_bedrock_permissions()
        self._add_memory_permissions(memory_resource_arn)
        self._add_ssm_permissions(parameter_namespace)
        self._add_support_permissions()
        
        # Add DynamoDB permissions if cache table ARN is provided
        if cache_table_arn:
            self._add_dynamodb_permissions(cache_table_arn)

    def _add_cloudwatch_logs_permissions(self) -> None:
        """Add CloudWatch Logs permissions for AgentCore Runtime."""
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:DescribeLogStreams",
                    "logs:CreateLogGroup",
                ],
                resources=[
                    "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/runtimes/*"
                ],
            )
        )
        
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:DescribeLogGroups"],
                resources=["arn:aws:logs:*:*:log-group:*"],
            )
        )
        
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                ],
            )
        )

    def _add_xray_permissions(self) -> None:
        """Add X-Ray tracing permissions for AgentCore Runtime."""
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                resources=["*"],
            )
        )

    def _add_cloudwatch_metrics_permissions(self) -> None:
        """Add CloudWatch metrics permissions for AgentCore Runtime."""
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "cloudwatch:namespace": "bedrock-agentcore"
                    }
                },
            )
        )

    def _add_ecr_permissions(self, ecr_repository_arn: str = None) -> None:
        """Add ECR image pull permissions.

        Args:
            ecr_repository_arn: Optional ARN of the ECR repository. If provided,
                image pull permissions are scoped to this repo. Otherwise uses '*'.
        """
        # GetAuthorizationToken must be on "*" resource
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )

        # Image pull permissions scoped to specific repository if provided
        pull_resource = ecr_repository_arn if ecr_repository_arn else "*"
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchCheckLayerAvailability",
                ],
                resources=[pull_resource],
            )
        )

    def _add_service_quotas_permissions(self) -> None:
        """Add Service Quotas API permissions."""
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "servicequotas:ListServiceQuotas",
                    "servicequotas:GetServiceQuota",
                    "servicequotas:ListAWSDefaultServiceQuotas",
                    "servicequotas:GetAWSDefaultServiceQuota",
                    "servicequotas:ListServices",
                ],
                resources=["*"],
            )
        )

    def _add_cloudwatch_permissions(self) -> None:
        """Add CloudWatch metrics permissions."""
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "cloudwatch:GetMetricData",
                    "cloudwatch:GetMetricStatistics",
                    "cloudwatch:ListMetrics",
                ],
                resources=["*"],
            )
        )

    def _add_bedrock_permissions(self) -> None:
        """Add Bedrock API permissions."""
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:ListFoundationModels",
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )
        
        # Add AgentCore workload identity permissions
        from aws_cdk import Aws
        self.role.add_to_policy(
            iam.PolicyStatement(
                sid="GetAgentAccessToken",
                actions=[
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{Aws.REGION}:{Aws.ACCOUNT_ID}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{Aws.REGION}:{Aws.ACCOUNT_ID}:workload-identity-directory/default/workload-identity/*",
                ],
            )
        )

    def _add_memory_permissions(self, memory_resource_arn: str) -> None:
        """
        Add AgentCore Memory permissions scoped to specific resource.

        Args:
            memory_resource_arn: ARN of the memory resource to scope permissions to
        """
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                ],
                resources=[memory_resource_arn],
            )
        )

    def _add_ssm_permissions(self, parameter_namespace: str) -> None:
        """
        Add SSM Parameter Store read permissions.

        Args:
            parameter_namespace: SSM parameter namespace for scoped access
        """
        # Ensure namespace ends with /* for proper scoping
        namespace_pattern = (
            parameter_namespace if parameter_namespace.endswith("*") 
            else f"{parameter_namespace.rstrip('/')}/*"
        )
        
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ssm:GetParameter",
                    "ssm:GetParameters",
                ],
                resources=[f"arn:aws:ssm:*:*:parameter{namespace_pattern}"],
            )
        )

    def _add_dynamodb_permissions(self, table_arn: str) -> None:
        """
        Add DynamoDB read permissions for cache table.

        Args:
            table_arn: ARN of the cache table
        """
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                ],
                resources=[table_arn],
            )
        )

    def _add_support_permissions(self) -> None:
        """Add AWS Support API permissions for creating support cases."""
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "support:CreateCase",
                    "support:DescribeCases",
                    "support:DescribeSeverityLevels",
                    "support:DescribeServices",
                ],
                resources=["*"],
            )
        )
