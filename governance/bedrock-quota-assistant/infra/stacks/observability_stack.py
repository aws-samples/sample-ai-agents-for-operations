"""Observability stack for Bedrock AgentCore runtime monitoring."""

from aws_cdk import Stack, RemovalPolicy, Tags, CfnOutput
from aws_cdk import aws_logs as logs
from constructs import Construct

from custom_constructs.observability_stack import XRayTransactionSearchConstruct


class ObservabilityStack(Stack):
    """Observability stack for AgentCore runtime logging and tracing."""

    # Environment-specific configuration
    ENVIRONMENT_CONFIG = {
        "dev": {
            "log_retention_days": logs.RetentionDays.ONE_WEEK,
            "removal_policy": RemovalPolicy.DESTROY,
            "xray_retain_on_delete": False,
        },
        "staging": {
            "log_retention_days": logs.RetentionDays.ONE_MONTH,
            "removal_policy": RemovalPolicy.DESTROY,
            "xray_retain_on_delete": True,
        },
        "prod": {
            "log_retention_days": logs.RetentionDays.THREE_MONTHS,
            "removal_policy": RemovalPolicy.RETAIN,
            "xray_retain_on_delete": True,
        },
    }

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: str,
        runtime_id: str,
        runtime_arn: str,
        xray_indexing_percentage: int = 1,
        xray_retain_on_delete: bool = None,
        **kwargs
    ) -> None:
        """
        Initialize the Observability stack.

        Args:
            scope: CDK app or parent construct
            construct_id: Unique identifier for this stack
            environment: Deployment environment (dev, staging, prod)
            runtime_id: AgentCore runtime ID
            runtime_arn: AgentCore runtime ARN
            xray_indexing_percentage: Percentage of spans to index (0-100, default 1)
            xray_retain_on_delete: Whether to keep Transaction Search enabled on
                stack delete. Defaults to environment config (False for dev,
                True for staging/prod). Pass explicitly to override.
            **kwargs: Additional stack properties

        Raises:
            ValueError: If environment is not one of: dev, staging, prod
        """
        super().__init__(scope, construct_id, **kwargs)

        # Validate environment parameter
        if environment not in self.ENVIRONMENT_CONFIG:
            raise ValueError(
                f"Invalid environment '{environment}'. "
                f"Must be one of: {', '.join(self.ENVIRONMENT_CONFIG.keys())}"
            )

        # Store parameters
        self.env_name = environment
        self.runtime_id = runtime_id
        self.runtime_arn = runtime_arn

        # Get environment-specific configuration
        self.config = self.ENVIRONMENT_CONFIG[environment]

        # X-Ray configuration: use explicit value if provided, else environment default
        self.xray_indexing_percentage = xray_indexing_percentage
        self.xray_retain_on_delete = (
            xray_retain_on_delete
            if xray_retain_on_delete is not None
            else self.config["xray_retain_on_delete"]
        )

        # Create resources
        self._create_log_group()
        self._create_xray_transaction_search()
        self._create_delivery_sources()
        self._create_delivery_destinations()
        self._create_deliveries()
        self._apply_tags()
        self._create_outputs()




    def _create_log_group(self) -> None:
        """Create CloudWatch log group for AgentCore runtime logs."""
        log_group_name = f"/aws/vendedlogs/bedrock-agentcore/runtime/{self.runtime_id}"
        
        self.log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=log_group_name,
            retention=self.config["log_retention_days"],
            removal_policy=self.config["removal_policy"],
        )

    def _create_xray_transaction_search(self) -> None:
        """Enable X-Ray Transaction Search for trace viewing."""
        self.xray_construct = XRayTransactionSearchConstruct(
            self,
            "XRayTransactionSearch",
            indexing_percentage=self.xray_indexing_percentage,
            retain_on_delete=self.xray_retain_on_delete,
        )
    def _create_delivery_sources(self) -> None:
        """Create CloudWatch Logs Delivery sources for logs and traces."""
        # Create delivery source for application logs
        self.logs_delivery_source = logs.CfnDeliverySource(
            self,
            "LogsDeliverySource",
            name=f"{self.runtime_id}-logs-source",
            log_type="APPLICATION_LOGS",
            resource_arn=self.runtime_arn,
        )

        # Create delivery source for traces
        self.traces_delivery_source = logs.CfnDeliverySource(
            self,
            "TracesDeliverySource",
            name=f"{self.runtime_id}-traces-source",
            log_type="TRACES",
            resource_arn=self.runtime_arn,
        )
    def _create_delivery_destinations(self) -> None:
        """Create CloudWatch Logs Delivery destinations for logs and traces."""
        # Create delivery destination for CloudWatch Logs
        # CDK infers type from destination_resource_arn (log group = CWL)
        self.logs_delivery_destination = logs.CfnDeliveryDestination(
            self,
            "LogsDeliveryDestination",
            name=f"{self.runtime_id}-logs-destination",
            destination_resource_arn=self.log_group.log_group_arn,
        )

        # Create delivery destination for X-Ray
        # For X-Ray, we need to use add_property_override since CDK may not support
        # X-Ray destinations without a resource ARN in all versions
        self.traces_delivery_destination = logs.CfnDeliveryDestination(
            self,
            "TracesDeliveryDestination",
            name=f"{self.runtime_id}-traces-destination",
        )
        # Override the DeliveryDestinationType property directly in CloudFormation
        self.traces_delivery_destination.add_property_override(
            "DeliveryDestinationType", "XRAY"
        )
        # X-Ray delivery destination requires Transaction Search to be enabled first
        # (UpdateTraceSegmentDestination must complete before creating X-Ray deliveries)
        self.traces_delivery_destination.node.add_dependency(
            self.xray_construct.transaction_search_config
        )
    def _create_deliveries(self) -> None:
        """Create CloudWatch Logs Deliveries to connect sources to destinations."""
        # Create delivery for logs (connects logs source to logs destination)
        self.logs_delivery = logs.CfnDelivery(
            self,
            "LogsDelivery",
            delivery_source_name=self.logs_delivery_source.name,
            delivery_destination_arn=self.logs_delivery_destination.attr_arn,
        )
        # Explicit dependency to ensure source exists before delivery
        self.logs_delivery.add_dependency(self.logs_delivery_source)
        self.logs_delivery.add_dependency(self.logs_delivery_destination)

        # Create delivery for traces (connects traces source to traces destination)
        self.traces_delivery = logs.CfnDelivery(
            self,
            "TracesDelivery",
            delivery_source_name=self.traces_delivery_source.name,
            delivery_destination_arn=self.traces_delivery_destination.attr_arn,
        )
        # Explicit dependency to ensure source exists before delivery
        self.traces_delivery.add_dependency(self.traces_delivery_source)
        self.traces_delivery.add_dependency(self.traces_delivery_destination)




    def _apply_tags(self) -> None:
        """Apply tags to all resources in the stack."""
        Tags.of(self).add("Project", "BedrockQuotaAgent")
        Tags.of(self).add("Environment", self.env_name)
        Tags.of(self).add("agent-managed", "true")

    def _create_outputs(self) -> None:
        """Create CloudFormation outputs for easy access to observability resources."""
        # Construct the log group name (same pattern used in _create_log_group)
        log_group_name = f"/aws/vendedlogs/bedrock-agentcore/runtime/{self.runtime_id}"
        
        # Output log group name
        CfnOutput(
            self,
            "LogGroupName",
            value=self.log_group.log_group_name,
            description="CloudWatch log group name for AgentCore runtime logs",
        )

        # Output log group ARN
        CfnOutput(
            self,
            "LogGroupArn",
            value=self.log_group.log_group_arn,
            description="CloudWatch log group ARN",
        )

        # Output CloudWatch Logs console URL
        # URL encode the log group name: / becomes $252F
        encoded_log_group_name = log_group_name.replace("/", "$252F")
        logs_console_url = (
            f"https://{self.region}.console.aws.amazon.com/cloudwatch/home"
            f"?region={self.region}#logsV2:log-groups/log-group/{encoded_log_group_name}"
        )
        CfnOutput(
            self,
            "LogsConsoleUrl",
            value=logs_console_url,
            description="Direct link to CloudWatch Logs console",
        )

        # Output X-Ray console URL
        traces_console_url = (
            f"https://{self.region}.console.aws.amazon.com/cloudwatch/home"
            f"?region={self.region}#xray:traces"
        )
        CfnOutput(
            self,
            "TracesConsoleUrl",
            value=traces_console_url,
            description="Direct link to X-Ray traces console",
        )
