"""Custom resource construct for asynchronous cache population."""

from aws_cdk import custom_resources as cr, aws_iam as iam, aws_lambda as lambda_
from constructs import Construct


class AsyncCachePopulator(Construct):
    """CloudFormation custom resource that triggers cache population asynchronously."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        function: lambda_.Function,
    ) -> None:
        """
        Create custom resource for async cache population.

        Args:
            scope: Parent construct
            construct_id: Unique identifier
            function: Lambda function to invoke for population
        """
        super().__init__(scope, construct_id)

        # Create custom resource that invokes Lambda asynchronously
        # Using Event invocation type means the custom resource returns immediately
        # without waiting for Lambda completion, preventing stack blocking
        cr.AwsCustomResource(
            self,
            "Resource",
            on_create=cr.AwsSdkCall(
                service="Lambda",
                action="invoke",
                parameters={
                    "FunctionName": function.function_arn,
                    "InvocationType": "Event",  # Async invocation - fire and forget
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"AsyncCachePopulator-{construct_id}"
                ),
            ),
            # No UPDATE or DELETE logic - no-op for those operations
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["lambda:InvokeFunction"],
                    resources=[function.function_arn],
                )
            ]),
        )
