"""ECR repository construct for Bedrock Quota Agent."""

from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecr_assets as ecr_assets
from constructs import Construct


class AgentEcrRepository(Construct):
    """Create ECR repository and handle Docker image build/push."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        dockerfile_path: str = "src",
    ) -> None:
        """
        Create ECR repository and build Docker image.

        Args:
            scope: Parent construct
            construct_id: Unique identifier
            dockerfile_path: Path to directory containing Dockerfile relative to project root
        """
        super().__init__(scope, construct_id)

        # Build and push Docker image using DockerImageAsset
        # This automatically creates an ECR repository and pushes the image
        self.image = ecr_assets.DockerImageAsset(
            self,
            "Image",
            directory=dockerfile_path,
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        # Expose image URI and repository
        self.image_uri = self.image.image_uri
        self.repository = self.image.repository
