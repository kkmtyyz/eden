import { execSync } from "child_process";
import * as path from "path";
import { fileURLToPath } from "url";
import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as s3vectors from "aws-cdk-lib/aws-s3vectors";
import * as bedrock from "aws-cdk-lib/aws-bedrock";
import * as lambdaEventSources from "aws-cdk-lib/aws-lambda-event-sources";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export interface ApiStackProps {
  userPool: cognito.IUserPool;
  userPoolClient: cognito.IUserPoolClient;
}

export class ApiStack extends Construct {
  public readonly apiEndpoint: string;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id);

    // ─── DynamoDB ─────────────────────────────────────────────────
    const objectsTable = new dynamodb.Table(this, "ObjectsTable", {
      partitionKey: { name: "object_id", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const jobsTable = new dynamodb.Table(this, "JobsTable", {
      partitionKey: { name: "job_id", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ─── S3 (画像バケット) ────────────────────────────────────────
    const imageBucket = new s3.Bucket(this, "ImageBucket", {
      cors: [
        {
          allowedMethods: [
            s3.HttpMethods.PUT,
            s3.HttpMethods.GET,
            s3.HttpMethods.HEAD,
          ],
          allowedOrigins: ["*"],
          allowedHeaders: ["*"],
          exposedHeaders: ["ETag", "x-amz-request-id"],
        },
      ],
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // ─── SQS (登録ジョブキュー) ───────────────────────────────────
    const registerQueue = new sqs.Queue(this, "RegisterQueue", {
      visibilityTimeout: cdk.Duration.minutes(15),
      retentionPeriod: cdk.Duration.days(3),
    });

    // ─── S3 Vectors (ベクトル検索) ────────────────────────────────
    // バケット名にスタック名のハッシュを含めて sandbox/本番で衝突しないようにする
    const stackId = cdk.Fn.select(2, cdk.Fn.split("/", cdk.Aws.STACK_ID));
    const shortHash = cdk.Fn.select(0, cdk.Fn.split("-", stackId));
    const vectorBucketName = `eden-vec-${shortHash}`;

    const vectorBucket = new s3vectors.CfnVectorBucket(this, "VectorBucket", {
      vectorBucketName,
    });
    vectorBucket.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

    const vectorIndex = new s3vectors.CfnIndex(this, "VectorIndex", {
      vectorBucketName,
      indexName: "eden-index-v2",
      dataType: "float32",
      dimension: 1024,
      distanceMetric: "cosine",
      metadataConfiguration: {
        nonFilterableMetadataKeys: ["label", "source_file", "rotation"],
      },
    });
    vectorIndex.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);
    vectorIndex.addDependency(vectorBucket);

    // ─── Bedrock Guardrails (テキストガードレール) ──────────────────
    // ガードレールプロファイルARNを組み立て (APACリージョン用)
    const guardrailProfileArn = cdk.Stack.of(this).formatArn({
      service: "bedrock",
      resource: "guardrail-profile",
      resourceName: "apac.guardrail.v1:0",
      arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
    });

    const textGuardrail = new bedrock.CfnGuardrail(this, "TextGuardrail", {
      name: "eden-text-guardrail",
      description: "Eden コメント入力に対するコンテンツフィルター (Standard Tier, 日本語対応)",
      blockedInputMessaging: "このコメントは不適切な内容を含むため投稿できません。",
      blockedOutputsMessaging: "不適切な内容が検出されました。",
      crossRegionConfig: {
        guardrailProfileArn,
      },
      contentPolicyConfig: {
        filtersConfig: [
          { type: "HATE", inputStrength: "MEDIUM", outputStrength: "NONE" },
          { type: "INSULTS", inputStrength: "MEDIUM", outputStrength: "NONE" },
          { type: "SEXUAL", inputStrength: "HIGH", outputStrength: "NONE" },
          { type: "VIOLENCE", inputStrength: "MEDIUM", outputStrength: "NONE" },
          { type: "MISCONDUCT", inputStrength: "MEDIUM", outputStrength: "NONE" },
        ],
        contentFiltersTierConfig: {
          tierName: "STANDARD",
        },
      },
    });

    // ─── Lambda Layer (eden パッケージ) ───────────────────────────
    const edenLayer = new lambda.LayerVersion(this, "EdenLayer", {
      code: lambda.Code.fromAsset(path.join(__dirname, "../../eden"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          local: {
            tryBundle(outputDir: string): boolean {
              try {
                const edenDir = path.join(__dirname, "../../eden");
                execSync(
                  [
                    "pip3 install",
                    "--platform manylinux2014_x86_64",
                    "--implementation cp",
                    "--python-version 3.12",
                    "--only-binary=:all:",
                    "-r requirements.txt",
                    `-t ${outputDir}/python`,
                  ].join(" "),
                  { cwd: edenDir, stdio: "inherit" }
                );
                execSync(`cp -r ${edenDir} ${outputDir}/python/eden`);
                // requirements.txt と __pycache__ を除外
                execSync(`rm -f ${outputDir}/python/eden/requirements.txt`);
                execSync(`rm -rf ${outputDir}/python/eden/__pycache__`);
                return true;
              } catch {
                return false;
              }
            },
          },
          command: [
            "bash",
            "-c",
            "pip install -r requirements.txt -t /asset-output/python && " +
              "cp -r . /asset-output/python/eden && " +
              "rm -f /asset-output/python/eden/requirements.txt && " +
              "rm -rf /asset-output/python/eden/__pycache__",
          ],
        },
      }),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: "eden common module layer",
    });

    // ─── Lambda 共通設定 ──────────────────────────────────────────
    const commonLambdaProps: Partial<lambda.FunctionProps> = {
      runtime: lambda.Runtime.PYTHON_3_12,
      layers: [edenLayer],
      timeout: cdk.Duration.seconds(29),
      memorySize: 512,
      environment: {
        OBJECTS_TABLE: objectsTable.tableName,
        JOBS_TABLE: jobsTable.tableName,
        IMAGE_BUCKET: imageBucket.bucketName,
        REGISTER_QUEUE_URL: registerQueue.queueUrl,
        EDEN_VECTOR_BUCKET: vectorBucketName,
        EDEN_VECTOR_INDEX: "eden-index-v2",
        EDEN_TEXT_GUARDRAIL_ID: textGuardrail.attrGuardrailId,
        EDEN_TEXT_GUARDRAIL_VERSION: textGuardrail.attrVersion,
        EDEN_TEXT_GUARDRAIL_REGION: cdk.Aws.REGION,
      },
    };

    const functionsDir = path.join(__dirname, "../functions");

    // ─── Lambda Functions ─────────────────────────────────────────
    const uploadUrlFn = new lambda.Function(this, "UploadUrlFn", {
      ...commonLambdaProps,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(functionsDir, "upload_url")),
    } as lambda.FunctionProps);

    const bboxCandidatesFn = new lambda.Function(this, "BboxCandidatesFn", {
      ...commonLambdaProps,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(functionsDir, "bbox_candidates")),
      timeout: cdk.Duration.seconds(29),
      memorySize: 1024,
    } as lambda.FunctionProps);

    const registerFn = new lambda.Function(this, "RegisterFn", {
      ...commonLambdaProps,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(functionsDir, "register")),
    } as lambda.FunctionProps);

    const registerWorkerFn = new lambda.Function(this, "RegisterWorkerFn", {
      ...commonLambdaProps,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(functionsDir, "register_worker")),
      timeout: cdk.Duration.minutes(15),
      memorySize: 1024,
    } as lambda.FunctionProps);

    const jobStatusFn = new lambda.Function(this, "JobStatusFn", {
      ...commonLambdaProps,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(functionsDir, "job_status")),
    } as lambda.FunctionProps);

    const searchFn = new lambda.Function(this, "SearchFn", {
      ...commonLambdaProps,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(functionsDir, "search")),
      timeout: cdk.Duration.seconds(29),
      memorySize: 1024,
    } as lambda.FunctionProps);

    const addInfoFn = new lambda.Function(this, "AddInfoFn", {
      ...commonLambdaProps,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(functionsDir, "add_info")),
    } as lambda.FunctionProps);

    const downloadUrlFn = new lambda.Function(this, "DownloadUrlFn", {
      ...commonLambdaProps,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(functionsDir, "download_url")),
    } as lambda.FunctionProps);

    // ─── 運用ツール: リセット Lambda ──────────────────────────────
    const resetDataFn = new lambda.Function(this, "ResetDataFn", {
      ...commonLambdaProps,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(functionsDir, "reset_data")),
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      description: "運用ツール: S3 Vectors + DynamoDB を空にする",
    } as lambda.FunctionProps);

    // ─── SQS Trigger ──────────────────────────────────────────────
    registerWorkerFn.addEventSource(
      new lambdaEventSources.SqsEventSource(registerQueue, {
        batchSize: 1,
      })
    );

    // ─── IAM Permissions ──────────────────────────────────────────
    // DynamoDB
    objectsTable.grantReadWriteData(registerWorkerFn);
    objectsTable.grantReadWriteData(addInfoFn);
    objectsTable.grantReadWriteData(resetDataFn);
    objectsTable.grantReadData(searchFn);
    jobsTable.grantReadWriteData(registerFn);
    jobsTable.grantReadWriteData(registerWorkerFn);
    jobsTable.grantReadWriteData(resetDataFn);
    jobsTable.grantReadData(jobStatusFn);

    // S3
    imageBucket.grantPut(uploadUrlFn);
    imageBucket.grantReadWrite(registerWorkerFn);
    imageBucket.grantRead(bboxCandidatesFn);
    imageBucket.grantRead(searchFn);
    imageBucket.grantRead(downloadUrlFn);

    // SQS
    registerQueue.grantSendMessages(registerFn);

    // Bedrock (BBox検出, 文字抽出, 埋め込み)
    const bedrockPolicy = new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel"],
      resources: ["*"],
    });
    bboxCandidatesFn.addToRolePolicy(bedrockPolicy);
    registerWorkerFn.addToRolePolicy(bedrockPolicy);
    searchFn.addToRolePolicy(bedrockPolicy);

    // Rekognition (ガードレール)
    const rekognitionPolicy = new iam.PolicyStatement({
      actions: [
        "rekognition:DetectFaces",
        "rekognition:DetectLabels",
        "rekognition:DetectModerationLabels",
      ],
      resources: ["*"],
    });
    registerWorkerFn.addToRolePolicy(rekognitionPolicy);

    // Bedrock Guardrails (テキストガードレール)
    // クロスリージョン推論ではガードレール本体 + ルーティング先プロファイルの両方が必要
    const bedrockGuardrailPolicy = new iam.PolicyStatement({
      actions: ["bedrock:ApplyGuardrail"],
      resources: [
        textGuardrail.attrGuardrailArn,
        guardrailProfileArn,
        // ルーティング先リージョンのプロファイルにもアクセスが必要
        `arn:aws:bedrock:*:${cdk.Aws.ACCOUNT_ID}:guardrail-profile/apac.guardrail.v1:0`,
      ],
    });
    registerFn.addToRolePolicy(bedrockGuardrailPolicy);
    addInfoFn.addToRolePolicy(bedrockGuardrailPolicy);

    // S3 Vectors
    const s3VectorsPolicy = new iam.PolicyStatement({
      actions: [
        "s3vectors:PutVectors",
        "s3vectors:QueryVectors",
        "s3vectors:GetVectors",
        "s3vectors:DeleteVectors",
        "s3vectors:ListVectors",
      ],
      resources: ["*"],
    });
    registerWorkerFn.addToRolePolicy(s3VectorsPolicy);
    searchFn.addToRolePolicy(s3VectorsPolicy);
    resetDataFn.addToRolePolicy(s3VectorsPolicy);

    // ─── API Gateway ──────────────────────────────────────────────
    const api = new apigateway.RestApi(this, "EdenApi", {
      restApiName: "Eden API",
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ["Content-Type", "Authorization"],
      },
    });

    // Cognito Authorizer
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(
      this,
      "CognitoAuthorizer",
      {
        cognitoUserPools: [props.userPool as cognito.UserPool],
      }
    );

    const authMethodOptions: apigateway.MethodOptions = {
      authorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
    };

    // Routes
    const uploadUrl = api.root.addResource("upload-url");
    uploadUrl.addMethod(
      "POST",
      new apigateway.LambdaIntegration(uploadUrlFn),
      authMethodOptions
    );

    const bboxCandidates = api.root.addResource("bbox-candidates");
    bboxCandidates.addMethod(
      "POST",
      new apigateway.LambdaIntegration(bboxCandidatesFn),
      authMethodOptions
    );

    const register = api.root.addResource("register");
    register.addMethod(
      "POST",
      new apigateway.LambdaIntegration(registerFn),
      authMethodOptions
    );

    const jobs = api.root.addResource("jobs");
    const jobId = jobs.addResource("{id}");
    jobId.addMethod(
      "GET",
      new apigateway.LambdaIntegration(jobStatusFn),
      authMethodOptions
    );

    const search = api.root.addResource("search");
    search.addMethod(
      "POST",
      new apigateway.LambdaIntegration(searchFn),
      authMethodOptions
    );

    const objects = api.root.addResource("objects");
    const objectId = objects.addResource("{id}");
    const info = objectId.addResource("info");
    info.addMethod(
      "POST",
      new apigateway.LambdaIntegration(addInfoFn),
      authMethodOptions
    );

    const downloadUrl = api.root.addResource("download-url");
    downloadUrl.addMethod(
      "POST",
      new apigateway.LambdaIntegration(downloadUrlFn),
      authMethodOptions
    );

    this.apiEndpoint = api.url;
  }
}
