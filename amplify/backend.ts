import { defineBackend } from "@aws-amplify/backend";
import { auth } from "./auth/resource.js";
import { ApiStack } from "./custom/api-stack.js";

const backend = defineBackend({
  auth,
});

// ─── 招待制: セルフサインアップ無効 ───────────────────────────
const cfnUserPool = backend.auth.resources.cfnResources.cfnUserPool;
cfnUserPool.adminCreateUserConfig = {
  allowAdminCreateUserOnly: true,
};

// カスタムスタック: API Gateway + Lambda + SQS + DynamoDB + S3
const apiStack = new ApiStack(
  backend.createStack("ApiStack"),
  "ApiStack",
  {
    userPool: backend.auth.resources.userPool,
    userPoolClient: backend.auth.resources.userPoolClient,
  }
);

// フロントにAPIエンドポイントを公開
backend.addOutput({
  custom: {
    apiEndpoint: apiStack.apiEndpoint,
  },
});
