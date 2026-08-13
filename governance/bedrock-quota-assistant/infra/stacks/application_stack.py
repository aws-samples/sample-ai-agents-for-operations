"""Application stack for Bedrock Quota Agent deployment."""

from pathlib import Path

from aws_cdk import Stack, CfnOutput, Tags
from constructs import Construct

from custom_constructs.application_stack.memory_resource import AgentMemoryResource
from custom_constructs.application_stack.iam_role import AgentIamRole
from custom_constructs.application_stack.ssm_parameters import AgentSsmParameters
from custom_constructs.application_stack.ecr_repository import AgentEcrRepository
from custom_constructs.application_stack.agentcore_runtime import AgentCoreRuntime

_INFRA_DIR = Path(__file__).resolve().parent.parent
_SRC_DIR = str(_INFRA_DIR.parent / "src")


class ApplicationStack(Stack):
    """Application stack for Bedrock Quota Agent deployment to AgentCore runtime."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: str,
        cache_table_name: str = None,
        cache_table_arn: str = None,
        **kwargs
    ) -> None:
        """
        Initialize the Application stack.

        Args:
            scope: CDK app or parent construct
            construct_id: Unique identifier for this stack
            environment: Deployment environment (dev, staging, prod)
            cache_table_name: Optional name of the cache table for SSM parameter
            cache_table_arn: Optional ARN of the cache table for IAM permissions
            **kwargs: Additional stack properties
        """
        super().__init__(scope, construct_id, **kwargs)

        # Validate environment parameter
        if not environment or not isinstance(environment, str):
            raise ValueError("environment parameter must be a non-empty string")

        self.env_name = environment
        self.cache_table_name = cache_table_name
        self.cache_table_arn = cache_table_arn

        # Create resources in dependency order
        self._create_memory_resource()
        self._create_ecr_repository()
        self._create_iam_role()
        self._create_ssm_parameters()
        self._create_agentcore_runtime()
        self._create_outputs()
        self._apply_tags()

    def _create_memory_resource(self) -> None:
        """Create AgentCore Memory resource."""
        # Get memory description from context or use default
        memory_description = self.node.try_get_context("memory_description")
        
        self.memory_resource = AgentMemoryResource(
            self,
            "MemoryResource",
            stack_name=self.stack_name,
            description=memory_description,
        )
        
        # Store memory attributes for easy access
        self.memory_id = self.memory_resource.memory_id
        self.memory_arn = self.memory_resource.memory_arn

    def _create_iam_role(self) -> None:
        """Create IAM role with necessary permissions."""
        self.iam_role = AgentIamRole(
            self,
            "IamRole",
            memory_resource_arn=self.memory_arn,
            parameter_namespace="/bedrock-quota-agent/",
            cache_table_arn=self.cache_table_arn,
            ecr_repository_arn=self.ecr_repository.repository.repository_arn,
        )

    def _create_ssm_parameters(self) -> None:
        """Create SSM parameters for configuration."""
        parameters = {
            "memory-id": self.memory_id,
            "region": self.region,
            "role-arn": self.iam_role.role.role_arn,
        }
        
        # Add cache table name if provided
        if self.cache_table_name:
            parameters["cache-table-name"] = self.cache_table_name
        
        self.ssm_parameters = AgentSsmParameters(
            self,
            "SsmParameters",
            parameters=parameters,
            parameter_namespace="/bedrock-quota-agent/",
        )

    def _create_ecr_repository(self) -> None:
        """Create ECR repository and build/push Docker image."""
        self.ecr_repository = AgentEcrRepository(
            self,
            "EcrRepository",
            dockerfile_path=_SRC_DIR,
        )
        
        # Store image URI for easy access
        self.image_uri = self.ecr_repository.image_uri

    def _create_agentcore_runtime(self) -> None:
        """Create AgentCore runtime resource."""
        self.agentcore_runtime = AgentCoreRuntime(
            self,
            "AgentCoreRuntime",
            image_uri=self.image_uri,
            role_arn=self.iam_role.role.role_arn,
            memory_id=self.memory_id,
        )
        
        # Add explicit dependency on IAM role to ensure policies are attached
        self.agentcore_runtime.node.add_dependency(self.iam_role.role)
        
        # Store runtime ARN and ID for easy access
        self.runtime_arn = self.agentcore_runtime.runtime_arn
        self.runtime_id = self.agentcore_runtime.runtime.get_att_string("RuntimeId")

    def _create_outputs(self) -> None:
        """Create CloudFormation outputs."""
        CfnOutput(
            self,
            "RuntimeArn",
            value=self.runtime_arn,
            description="ARN of the AgentCore runtime",
            export_name=f"{self.stack_name}-RuntimeArn",
        )
        
        CfnOutput(
            self,
            "RuntimeId",
            value=self.runtime_id,
            description="ID of the AgentCore runtime",
            export_name=f"{self.stack_name}-RuntimeId",
        )
        
        CfnOutput(
            self,
            "RepositoryUri",
            value=self.ecr_repository.repository.repository_uri,
            description="URI of the ECR repository",
            export_name=f"{self.stack_name}-RepositoryUri",
        )
        
        CfnOutput(
            self,
            "RoleArn",
            value=self.iam_role.role.role_arn,
            description="ARN of the IAM role",
            export_name=f"{self.stack_name}-RoleArn",
        )
        
        CfnOutput(
            self,
            "MemoryId",
            value=self.memory_id,
            description="ID of the memory resource",
            export_name=f"{self.stack_name}-MemoryId",
        )
        
        CfnOutput(
            self,
            "MemoryArn",
            value=self.memory_arn,
            description="ARN of the memory resource",
            export_name=f"{self.stack_name}-MemoryArn",
        )

    def _apply_tags(self) -> None:
        """Apply tags to all resources in the stack."""
        Tags.of(self).add("Project", "BedrockQuotaAgent")
        Tags.of(self).add("Environment", self.env_name)
        Tags.of(self).add("agent-managed", "true")
