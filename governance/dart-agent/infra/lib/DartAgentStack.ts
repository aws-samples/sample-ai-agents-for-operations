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
      billing: dynamodb.Billing.onDemand(),
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
      description: "DART Agent ECS task role — read-only on customer data",
    });

    // READ-ONLY: Amazon Bedrock inference for the report narrative.
    // Grants the default model plus two approved alternatives selectable via MODEL_ID.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "BedrockInvokeApprovedModels",
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [
          `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-sonnet-4-20250514-v1:0`,
          `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-haiku-4-5`,
          `arn:aws:bedrock:${this.region}::foundation-model/amazon.nova-pro-v1:0`,
        ],
      })
    );

    // READ-ONLY: Amazon Comprehend PII detection (no resource-level permissions).
    // The agent uses the batch API (BatchDetectPiiEntities, up to 25 docs/call).
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "ComprehendPiiDetection",
        effect: iam.Effect.ALLOW,
        actions: ["comprehend:BatchDetectPiiEntities"],
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

    // Docker image built from local Dockerfile
    const image = new ecr_assets.DockerImageAsset(this, "DartImage", {
      directory: path.join(__dirname, "../.."),
      file: "Dockerfile",
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
        MODEL_ID: "anthropic.claude-sonnet-4-20250514-v1:0",
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
