// Copyright (c) 2026 Amazon Web Services
// Licensed under the MIT License
// See LICENSE file in the project root for full license information.

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import * as path from "path";

export interface DartAgentStackProps extends cdk.StackProps {
  environment: string;
}

export class DartAgentStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: DartAgentStackProps) {
    super(scope, id, props);

    const { environment } = props;

    // ── KMS key for encryption at rest ──────────────────────────────────────
    const encryptionKey = new kms.Key(this, "DartAgentKey", {
      description: "DART Agent encryption key",
      enableKeyRotation: true,
      removalPolicy:
        environment === "prod"
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
    });
    encryptionKey.addAlias(`alias/dart-agent-${environment}`);

    // Allow CloudWatch Logs to use the KMS key for the encrypted log group.
    // Without this grant, creating a KMS-encrypted log group fails with
    // "KMS key ... is not allowed to be used with Arn .../log-group:...".
    encryptionKey.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: "AllowCloudWatchLogs",
        effect: iam.Effect.ALLOW,
        principals: [
          new iam.ServicePrincipal(`logs.${this.region}.amazonaws.com`),
        ],
        actions: [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ],
        resources: ["*"],
        conditions: {
          ArnLike: {
            "kms:EncryptionContext:aws:logs:arn": `arn:aws:logs:${this.region}:${this.account}:log-group:*`,
          },
        },
      })
    );

    // ── S3 output bucket ─────────────────────────────────────────────────────
    const outputBucket = new s3.Bucket(this, "DartOutputBucket", {
      bucketName: `dart-agent-output-${this.account}-${this.region}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey,
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy:
        environment === "prod"
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: environment !== "prod",
      lifecycleRules: [
        {
          id: "expire-old-reports",
          expiration: cdk.Duration.days(365),
          prefix: "reports/",
        },
      ],
    });

    // ── DynamoDB audit trail table ───────────────────────────────────────────
    const auditTable = new dynamodb.Table(this, "DartAuditTable", {
      tableName: `dart-agent-audit-trail-${environment}`,
      partitionKey: { name: "run_id", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "timestamp", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey,
      pointInTimeRecovery: true,
      timeToLiveAttribute: "ttl",
      removalPolicy:
        environment === "prod"
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
    });

    // ── CloudWatch log group ─────────────────────────────────────────────────
    const logGroup = new logs.LogGroup(this, "DartLogGroup", {
      logGroupName: `/dart-agent/${environment}`,
      retention:
        environment === "prod"
          ? logs.RetentionDays.THREE_MONTHS
          : logs.RetentionDays.ONE_WEEK,
      encryptionKey,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ── VPC ──────────────────────────────────────────────────────────────────
    const vpc = new ec2.Vpc(this, "DartVpc", {
      vpcName: `dart-agent-vpc-${environment}`,
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: "Public",
          subnetType: ec2.SubnetType.PUBLIC,
        },
        {
          cidrMask: 24,
          name: "Private",
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
      ],
    });

    // VPC endpoints for AWS services (keep traffic within AWS network)
    vpc.addGatewayEndpoint("S3Endpoint", {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });
    vpc.addGatewayEndpoint("DynamoDBEndpoint", {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
    });

    // ── ECS cluster ──────────────────────────────────────────────────────────
    const cluster = new ecs.Cluster(this, "DartCluster", {
      clusterName: `dart-agent-${environment}`,
      vpc,
      containerInsights: true,
    });

    // ── IAM task role (principle of least privilege) ─────────────────────────
    const taskRole = new iam.Role(this, "DartTaskRole", {
      roleName: `dart-agent-task-role-${environment}`,
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      description: "DART Agent ECS task role - read-only on customer data",
    });

    // READ-ONLY: Amazon Bedrock inference for the report narrative.
    // Newer models (e.g. Claude Sonnet 4) are not invocable by their bare
    // foundation-model ID with on-demand throughput — they require a
    // cross-region inference profile (e.g. us.anthropic.claude-sonnet-4-...).
    // Invoking a "us." profile requires permission on BOTH the inference-profile
    // resource AND the underlying foundation-model in every region the profile
    // can route to (us-east-1, us-east-2, us-west-2).
    const bedrockProfileRegions = ["us-east-1", "us-east-2", "us-west-2"];
    const bedrockModelIds = [
      "anthropic.claude-sonnet-4-5-20250929-v1:0",
      "anthropic.claude-sonnet-4-20250514-v1:0",
      "anthropic.claude-haiku-4-5",
      "amazon.nova-pro-v1:0",
    ];
    const foundationModelArns = bedrockProfileRegions.flatMap((r) =>
      bedrockModelIds.map(
        (m) => `arn:aws:bedrock:${r}::foundation-model/${m}`
      )
    );
    const inferenceProfileArns = bedrockModelIds.map(
      (m) => `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/us.${m}`
    );
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "BedrockInvokeApprovedModels",
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [...foundationModelArns, ...inferenceProfileArns],
      })
    );

    // READ-ONLY: Amazon Comprehend PII detection (no resource-level permissions).
    // Comprehend has no batch PII API; the agent calls DetectPiiEntities per
    // document, so the required action is comprehend:DetectPiiEntities.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "ComprehendPiiDetection",
        effect: iam.Effect.ALLOW,
        actions: ["comprehend:DetectPiiEntities"],
        resources: ["*"], // Comprehend does not support resource-level permissions
      })
    );

    // WRITE: Agent-internal output bucket only (tagged agent-managed: true)
    outputBucket.grantReadWrite(taskRole);

    // WRITE: Agent-internal DynamoDB audit trail only
    auditTable.grantReadWriteData(taskRole);

    // WRITE: CloudWatch logs
    logGroup.grantWrite(taskRole);

    // READ: KMS key for decryption
    encryptionKey.grantDecrypt(taskRole);

    // X-Ray tracing
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "XRayTracing",
        effect: iam.Effect.ALLOW,
        actions: ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
        resources: ["*"],
      })
    );

    // ── ECS task definition ──────────────────────────────────────────────────
    const taskDefinition = new ecs.FargateTaskDefinition(
      this,
      "DartTaskDef",
      {
        family: `dart-agent-${environment}`,
        cpu: 4096,   // 4 vCPU
        memoryLimitMiB: 8192,  // 8 GB
        taskRole,
        runtimePlatform: {
          operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
          cpuArchitecture: ecs.CpuArchitecture.X86_64,
        },
      }
    );

    // Docker image built from local Dockerfile.
    // Pin the build platform to linux/amd64 so the image always matches the
    // X86_64 task definition below, even when built on an arm64 machine
    // (e.g. Apple Silicon). Without this, the task fails at startup with
    // "exec format error".
    const image = new ecr_assets.DockerImageAsset(this, "DartImage", {
      directory: path.join(__dirname, "../.."),
      file: "Dockerfile",
      platform: ecr_assets.Platform.LINUX_AMD64,
      buildArgs: {
        ENVIRONMENT: environment,
      },
    });

    taskDefinition.addContainer("DartContainer", {
      image: ecs.ContainerImage.fromDockerImageAsset(image),
      containerName: "dart-agent",
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "dart-agent",
        logGroup,
      }),
      environment: {
        AWS_REGION: this.region,
        AWS_ACCOUNT_ID: this.account,
        OUTPUT_BUCKET: outputBucket.bucketName,
        AUDIT_TABLE: auditTable.tableName,
        LOG_LEVEL: environment === "prod" ? "INFO" : "DEBUG",
        ENVIRONMENT: environment,
        MODEL_ID: "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        MAX_TOKENS: "4096",
        DEFAULT_MODE: "step-by-step",
        DEDUP_SIMILARITY_THRESHOLD: "0.85",
      },
      healthCheck: {
        command: ["CMD-SHELL", "python -c 'import agent' || exit 1"],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(10),
        retries: 3,
        startPeriod: cdk.Duration.seconds(60),
      },
      essential: true,
    });

    // ── Outputs ──────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, "OutputBucketName", {
      value: outputBucket.bucketName,
      description: "DART Agent output S3 bucket",
      exportName: `dart-agent-output-bucket-${environment}`,
    });

    new cdk.CfnOutput(this, "AuditTableName", {
      value: auditTable.tableName,
      description: "DART Agent DynamoDB audit trail table",
      exportName: `dart-agent-audit-table-${environment}`,
    });

    new cdk.CfnOutput(this, "ClusterName", {
      value: cluster.clusterName,
      description: "ECS cluster name",
      exportName: `dart-agent-cluster-${environment}`,
    });

    // Tag all resources
    cdk.Tags.of(this).add("project", "dart-agent");
    cdk.Tags.of(this).add("environment", environment);
    cdk.Tags.of(this).add("agent-managed", "true");
  }
}
