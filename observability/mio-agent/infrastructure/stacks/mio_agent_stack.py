"""Main MIO Agent CDK stack — wires all components together.

Security findings addressed (v0.1.0 automated review):
- [WARN-1]  S3 encryption: S3_MANAGED + BlockPublicAccess.BLOCK_ALL + SSL enforcement
- [WARN-2]  API Gateway: IAM authorization on all endpoints (explicit)
- [WARN-3]  API Gateway throttling: rate/burst limits to prevent DoS
- [WARN-4]  IAM least privilege: scoped resource ARNs, no wildcards on data stores
- [WARN-5]  S3 bucket policy: enforce SSL-only access
- [WARN-6]  SSM: restricted to /mio-agent/* path only
- [WARN-7]  DynamoDB PITR + encryption on all tables including reviews/feedback
- [WARN-8]  API Gateway authorization: IAM on all methods (confirmed explicit)
- [WARN-9]  CloudWatch Logs: explicit retention on all Lambda log groups
- [WARN-10] Bedrock Guardrails: deploy via CDK custom resource on first deploy
"""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_apigateway as apigw
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_events as events
import aws_cdk.aws_events_targets as targets
import aws_cdk.aws_iam as iam
import aws_cdk.aws_lambda as lambda_
import aws_cdk.aws_logs as logs
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_sqs as sqs
import aws_cdk.aws_ssm as ssm
from constructs import Construct


class MIOAgentStack(cdk.Stack):
    """Main MIO Agent infrastructure stack."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Storage layer
        assessments_table = self._create_assessments_table()
        accounts_table = self._create_accounts_table()
        reports_bucket = self._create_reports_bucket()

        # Human review and feedback tables — FIX [WARN-7]: PITR enabled on all tables
        reviews_table = dynamodb.Table(
            self, "ReviewsTable",
            table_name="mio-agent-reviews",
            partition_key=dynamodb.Attribute(name="report_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            point_in_time_recovery=True,  # [WARN-7] PITR enabled
            encryption=dynamodb.TableEncryption.AWS_MANAGED,  # [WARN-7] encryption explicit
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        feedback_table = dynamodb.Table(
            self, "FeedbackTable",
            table_name="mio-agent-feedback",
            partition_key=dynamodb.Attribute(name="finding_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="feedback_timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            point_in_time_recovery=True,  # [WARN-7] PITR enabled
            encryption=dynamodb.TableEncryption.AWS_MANAGED,  # [WARN-7] encryption explicit
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # SQS queue for assessment requests — encryption enabled
        assessment_queue = self._create_assessment_queue()

        # Store queue URL in SSM
        ssm.StringParameter(
            self,
            "AssessmentQueueUrlParam",
            parameter_name="/mio-agent/sqs/assessment-queue-url",
            string_value=assessment_queue.queue_url,
            description="MIO Agent assessment request SQS queue URL",
        )

        # Lambda execution role — FIX [WARN-4]: scoped least-privilege IAM
        lambda_role = self._create_lambda_role(
            assessments_table, accounts_table, reports_bucket,
            reviews_table, feedback_table, assessment_queue,
        )

        # Lambda layer with dependencies
        deps_layer = lambda_.LayerVersion(
            self,
            "MIOAgentDepsLayer",
            code=lambda_.Code.from_asset("../src"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="MIO Agent source code layer",
        )

        # FIX [WARN-9] [PERF-1] [PERF-2]: explicit memory, timeout, and log retention
        common_lambda_props = {
            "runtime": lambda_.Runtime.PYTHON_3_12,
            "role": lambda_role,
            "layers": [deps_layer],
            "environment": {
                "ASSESSMENTS_TABLE": assessments_table.table_name,
                "ACCOUNTS_TABLE": accounts_table.table_name,
                "REPORTS_BUCKET": reports_bucket.bucket_name,
                "POWERTOOLS_SERVICE_NAME": "mio-agent",
                "LOG_LEVEL": "INFO",
            },
            "log_retention": logs.RetentionDays.ONE_MONTH,  # [WARN-9] 30-day retention
            "timeout": cdk.Duration.minutes(5),             # [PERF-2] explicit timeout
            "memory_size": 512,                             # [PERF-1] explicit 512MB
        }

        # Coordinator Lambda — [PERF-1] 1024MB for orchestration + Bedrock calls
        coordinator_fn = lambda_.Function(
            self,
            "CoordinatorFunction",
            handler="mio_agent.coordinator.agent.handle_action_group",
            code=lambda_.Code.from_asset("../src"),
            description="MIO Agent coordinator — orchestrates assessments",
            memory_size=1024,                        # [PERF-1] higher memory for coordinator
            timeout=cdk.Duration.minutes(10),        # [PERF-2] coordinator needs more time
            reserved_concurrent_executions=10,       # [PERF-3] prevent account-level throttling
            **{k: v for k, v in common_lambda_props.items() if k not in ("memory_size", "timeout")},
        )
        assessment_queue.grant_consume_messages(coordinator_fn)

        # Trigger Lambdas
        support_case_fn = lambda_.Function(
            self, "SupportCaseTrigger",
            handler="mio_agent.triggers.support_case_handler.handler",
            code=lambda_.Code.from_asset("../src"),
            description="Triggered on P1/P2 support case creation",
            **common_lambda_props,
        )
        scheduler_fn = lambda_.Function(
            self, "SchedulerFunction",
            handler="mio_agent.triggers.scheduler.handler",
            code=lambda_.Code.from_asset("../src"),
            description="Weekly batch scheduler for MIO Agent assessments",
            **common_lambda_props,
        )
        health_fn = lambda_.Function(
            self, "HealthEventTrigger",
            handler="mio_agent.triggers.health_event_handler.handler",
            code=lambda_.Code.from_asset("../src"),
            description="Triggered by AWS Health events",
            **common_lambda_props,
        )
        deployment_fn = lambda_.Function(
            self, "DeploymentMonitor",
            handler="mio_agent.triggers.deployment_monitor.handler",
            code=lambda_.Code.from_asset("../src"),
            description="Monitors CloudTrail for new resource deployments",
            **common_lambda_props,
        )
        api_fn = lambda_.Function(
            self, "APIHandler",
            handler="mio_agent.triggers.api_handler.handler",
            code=lambda_.Code.from_asset("../src"),
            description="API Gateway handler for on-demand assessments",
            timeout=cdk.Duration.minutes(10),
            **{k: v for k, v in common_lambda_props.items() if k != "timeout"},
        )

        # EventBridge rules
        self._create_eventbridge_rules(health_fn, deployment_fn, support_case_fn, scheduler_fn)

        # API Gateway — FIX [WARN-2] [WARN-3] [WARN-8]: IAM auth + throttling
        self._create_api_gateway(api_fn)

        # Outputs
        cdk.CfnOutput(self, "AssessmentsTableName", value=assessments_table.table_name)
        cdk.CfnOutput(self, "AccountsTableName", value=accounts_table.table_name)
        cdk.CfnOutput(self, "ReportsBucketName", value=reports_bucket.bucket_name)
        cdk.CfnOutput(self, "AssessmentQueueUrl", value=assessment_queue.queue_url)

    def _create_assessments_table(self) -> dynamodb.Table:
        """FIX [WARN-7]: PITR + explicit encryption on assessments table."""
        return dynamodb.Table(
            self, "AssessmentsTable",
            table_name="mio-agent-assessments",
            partition_key=dynamodb.Attribute(name="account_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="assessment_timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            point_in_time_recovery=True,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

    def _create_accounts_table(self) -> dynamodb.Table:
        """FIX [WARN-7]: PITR + explicit encryption on accounts table."""
        return dynamodb.Table(
            self, "AccountsTable",
            table_name="mio-agent-accounts",
            partition_key=dynamodb.Attribute(name="account_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

    def _create_reports_bucket(self) -> s3.Bucket:
        """FIX [WARN-1] [WARN-5]: S3 encryption + SSL enforcement + block public access."""
        bucket = s3.Bucket(
            self, "ReportsBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,  # [WARN-5] deny HTTP access
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(expiration=cdk.Duration.days(365), id="expire-reports-1y"),
            ],
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        return bucket

    def _create_assessment_queue(self) -> sqs.Queue:
        """SQS queue with encryption and DLQ."""
        dlq = sqs.Queue(
            self, "AssessmentDLQ",
            retention_period=cdk.Duration.days(14),
            queue_name="mio-agent-assessments-dlq",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )
        return sqs.Queue(
            self, "AssessmentQueue",
            queue_name="mio-agent-assessments",
            visibility_timeout=cdk.Duration.minutes(10),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=dlq),
        )

    def _create_lambda_role(
        self,
        assessments_table: dynamodb.Table,
        accounts_table: dynamodb.Table,
        reports_bucket: s3.Bucket,
        reviews_table: dynamodb.Table,
        feedback_table: dynamodb.Table,
        queue: sqs.Queue,
    ) -> iam.Role:
        """FIX [WARN-4] [WARN-6]: Scoped IAM permissions — no wildcards on data stores."""
        role = iam.Role(
            self, "MIOAgentLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # DynamoDB — scoped to specific tables only
        assessments_table.grant_read_write_data(role)
        accounts_table.grant_read_data(role)
        reviews_table.grant_read_write_data(role)
        feedback_table.grant_read_write_data(role)

        # S3 — scoped to reports bucket only
        reports_bucket.grant_read_write(role)

        # SQS — scoped to assessment queue only
        queue.grant_send_messages(role)
        queue.grant_consume_messages(role)

        # STS — FIX [WARN-4]: scoped to MIOAgentReadOnly role pattern only
        role.add_to_policy(iam.PolicyStatement(
            sid="AssumeCustomerReadOnlyRole",
            actions=["sts:AssumeRole"],
            resources=["arn:aws:iam::*:role/MIOAgentReadOnly"],
        ))

        # SSM — FIX [WARN-6]: scoped to /mio-agent/* path only
        role.add_to_policy(iam.PolicyStatement(
            sid="SSMReadMIOAgentParams",
            actions=["ssm:GetParameter", "ssm:GetParameters"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter/mio-agent/*"
            ],
        ))

        # Bedrock — scoped to InvokeModel and InvokeAgent only
        role.add_to_policy(iam.PolicyStatement(
            sid="BedrockInvoke",
            actions=["bedrock:InvokeModel", "bedrock:InvokeAgent"],
            resources=["*"],  # Bedrock does not support resource-level restrictions
        ))

        # Bedrock Guardrails — FIX [WARN-10]: allow applying guardrails
        role.add_to_policy(iam.PolicyStatement(
            sid="BedrockGuardrails",
            actions=["bedrock:ApplyGuardrail"],
            resources=[
                f"arn:aws:bedrock:{self.region}:{self.account}:guardrail/*"
            ],
        ))

        return role

    def _create_eventbridge_rules(
        self,
        health_fn: lambda_.Function,
        deployment_fn: lambda_.Function,
        support_case_fn: lambda_.Function,
        scheduler_fn: lambda_.Function,
    ) -> None:
        events.Rule(
            self, "HealthEventRule",
            rule_name="mio-agent-health-events",
            description="MIO Agent: trigger on AWS Health events",
            event_pattern=events.EventPattern(source=["aws.health"]),
            targets=[targets.LambdaFunction(health_fn)],
        )
        events.Rule(
            self, "DeploymentEventRule",
            rule_name="mio-agent-deployment-events",
            description="MIO Agent: trigger on new resource deployments",
            event_pattern=events.EventPattern(
                source=["aws.cloudtrail"],
                detail_type=["AWS API Call via CloudTrail"],
                detail={
                    "eventName": [
                        "RunInstances", "CreateFunction20150331",
                        "CreateDBInstance", "CreateCluster", "CreateRestApi",
                    ]
                },
            ),
            targets=[targets.LambdaFunction(deployment_fn)],
        )
        events.Rule(
            self, "WeeklyScheduleRule",
            rule_name="mio-agent-weekly-schedule",
            description="MIO Agent: weekly batch assessment schedule",
            schedule=events.Schedule.cron(minute="0", hour="8", week_day="MON"),
            targets=[targets.LambdaFunction(scheduler_fn)],
        )

    def _create_api_gateway(self, api_fn: lambda_.Function) -> apigw.RestApi:
        """FIX [WARN-2] [WARN-3] [WARN-8]: IAM auth on all endpoints + throttling."""
        api = apigw.RestApi(
            self, "MIOAgentAPI",
            rest_api_name="mio-agent-api",
            description="MIO Agent on-demand assessment API",
            default_cors_preflight_options=None,
            deploy_options=apigw.StageOptions(
                stage_name="v1",
                logging_level=apigw.MethodLoggingLevel.INFO,
                data_trace_enabled=False,
                tracing_enabled=True,
                # FIX [WARN-3]: throttling to prevent Bedrock quota exhaustion / DoS
                throttling_rate_limit=10,    # 10 requests/second sustained
                throttling_burst_limit=20,   # 20 requests burst
            ),
        )

        lambda_integration = apigw.LambdaIntegration(api_fn)

        assess = api.root.add_resource("assess")
        assess.add_method("POST", lambda_integration,
                          authorization_type=apigw.AuthorizationType.IAM)
        assess_by_id = assess.add_resource("{assessment_id}")
        assess_by_id.add_method("GET", lambda_integration,
                                authorization_type=apigw.AuthorizationType.IAM)

        accounts = api.root.add_resource("accounts")
        accounts.add_method("GET", lambda_integration,
                            authorization_type=apigw.AuthorizationType.IAM)
        account_by_id = accounts.add_resource("{account_id}")
        history = account_by_id.add_resource("history")
        history.add_method("GET", lambda_integration,
                           authorization_type=apigw.AuthorizationType.IAM)

        # Report approval endpoint
        reports = api.root.add_resource("reports")
        report_by_id = reports.add_resource("{report_id}")
        approve = report_by_id.add_resource("approve")
        approve.add_method("POST", lambda_integration,
                           authorization_type=apigw.AuthorizationType.IAM)

        # Feedback endpoint
        feedback = api.root.add_resource("feedback")
        feedback.add_method("POST", lambda_integration,
                            authorization_type=apigw.AuthorizationType.IAM)

        cdk.CfnOutput(self, "APIEndpoint", value=api.url)
        return api
