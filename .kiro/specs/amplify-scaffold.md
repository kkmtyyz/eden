# Spec: Amplify プロジェクト スキャフォールド

Amplify Gen2 プロジェクトの初期構成を作成する。CDKカスタムスタックで API Gateway REST + Lambda群 + SQS + DynamoDB + S3 を定義し、`npx ampx sandbox` で動作する状態にする。

## Requirements

### R1: Amplify Gen2 プロジェクト初期化
- `amplify/` ディレクトリに Amplify Gen2 の backend 定義を配置する
- `auth` は Cognito (`defineAuth`) で定義。**招待制**（セルフサインアップ無効、管理者がユーザー作成）
- カスタムスタック (`backend.createStack`) で以下リソースを定義:
  - API Gateway REST + Cognito オーソライザー
  - Lambda 群 (Python 3.12 ランタイム)
  - SQS キュー (登録ジョブ)
  - DynamoDB テーブル x2 (物体テーブル、ジョブテーブル)
  - S3 バケット (画像)
- 使用バージョン:
  - aws-amplify: ^6.18
  - @aws-amplify/backend: ^1
  - aws-cdk-lib: ^2.261.0

### R2: Lambda 群の骨格
以下のLambda関数の骨格(handler + eden パッケージ呼び出し構造)を作成する:
1. **upload_url** — POST /upload-url: presigned PUT URL 発行
2. **bbox_candidates** — POST /bbox-candidates: BBox検出 → 候補返却
3. **register** — POST /register: SQSへジョブ投入、job_id返却 (202)
4. **register_worker** — SQS trigger: ガードレール → クロップ → 文字抽出 → 埋め込み → S3 Vectors/DynamoDB登録
5. **job_status** — GET /jobs/{id}: ジョブ状態返却
6. **search** — POST /search: BBox検出 → 並列処理 → 3値判定 → 結果返却
7. **add_info** — POST /objects/{id}/info: DynamoDB info 追記

### R3: eden パッケージの Lambda Layer 化
- 標準の `lambda.LayerVersion` + `Code.fromAsset` + `bundling.local` (`tryBundle`) を使用
- Docker 不要。`pip install --platform manylinux2014_x86_64 --only-binary=:all:` でLambda向けwheelを取得
- Amplify CI/CD でもローカルでも誰でもビルド可能
- Lambda 関数から `from eden.xxx import yyy` で利用できること

### R4: フロント (Next.js) の骨格
- Next.js (App Router) のプロジェクト骨格を作成
- `amplify_outputs.json` から API エンドポイントを読み取る構成
- スマホ向けレスポンシブ (実質SPA)
- Cognito 認証連携 (Amplify UI の Authenticator)

### R5: `npx ampx sandbox` で動作
- auth + カスタムスタックが CloudFormation でデプロイ可能な状態
- sandbox 環境で API Gateway → Lambda → DynamoDB/S3 の基本疎通が通ること

## Design

### ディレクトリ構成

```
eden/                        ← 既存: 共通モジュール (Lambda Layer ソースも兼ねる)
├── requirements.txt         ← Layer ビルド用 (boto3, numpy, Pillow)
experiments/                 ← 既存: PoC実験スクリプト
amplify/
├── auth/
│   └── resource.ts          # defineAuth (Cognito, 招待制)
├── backend.ts               # defineBackend + カスタムスタック
├── custom/
│   └── api-stack.ts         # API Gateway + Lambda + SQS + DynamoDB + S3
├── functions/
│   ├── upload_url/
│   │   └── handler.py
│   ├── bbox_candidates/
│   │   └── handler.py
│   ├── register/
│   │   └── handler.py
│   ├── register_worker/
│   │   └── handler.py
│   ├── job_status/
│   │   └── handler.py
│   ├── search/
│   │   └── handler.py
│   └── add_info/
│       └── handler.py
├── package.json
└── tsconfig.json
app/                         ← Next.js App Router
├── layout.tsx
├── page.tsx
├── amplify-config.ts
└── ...
package.json                 ← ルート (Next.js + Amplify)
tsconfig.json
next.config.js
```

### DynamoDB テーブル設計

**物体テーブル (ObjectsTable)**
| 属性 | 型 | キー |
|---|---|---|
| object_id | S | PK |
| thumbnail_key | S | — |
| info | L (list of maps) | — |

info の各要素: `{text: S, user_id: S, created_at: S}`

**ジョブテーブル (JobsTable)**
| 属性 | 型 | キー |
|---|---|---|
| job_id | S | PK |
| status | S | — |
| object_id | S | — |
| created_at | S | — |
| error | S | — |

status: `pending` → `processing` → `completed` / `failed`

### 認証 (Cognito)

```typescript
// amplify/auth/resource.ts
import { defineAuth } from "@aws-amplify/backend";

export const auth = defineAuth({
  loginWith: { email: true },
  // セルフサインアップ無効 → 管理者がCognitoコンソールからユーザー作成
  // CDKオーバーライドで allowAdminCreateUserOnly: true に設定
});
```

招待制の実現: Cognito User Pool の `AdminCreateUserConfig.AllowAdminCreateUserOnly = true` をCDKオーバーライドで設定。ユーザーは管理者がCognitoコンソールまたはCLIから作成し、一時パスワードがメールで届く。

```
POST   /upload-url          → upload_url Lambda
POST   /bbox-candidates     → bbox_candidates Lambda
POST   /register            → register Lambda
GET    /jobs/{id}           → job_status Lambda
POST   /search              → search Lambda
POST   /objects/{id}/info   → add_info Lambda
```

全エンドポイントに Cognito User Pool オーソライザーを適用。

### Lambda Layer 構成 (`lambda.LayerVersion` + `bundling.local`)

```typescript
// amplify/custom/api-stack.ts (抜粋)
import { execSync } from "child_process";
import * as lambda from "aws-cdk-lib/aws-lambda";

const edenLayer = new lambda.LayerVersion(this, "EdenLayer", {
  code: lambda.Code.fromAsset(path.join(__dirname, "../../eden"), {
    bundling: {
      image: lambda.Runtime.PYTHON_3_12.bundlingImage, // Dockerフォールバック用
      local: {
        tryBundle(outputDir: string): boolean {
          execSync([
            "pip3 install",
            "--platform manylinux2014_x86_64",
            "--implementation cp",
            "--python-version 3.12",
            "--only-binary=:all:",
            "-r requirements.txt",
            `-t ${outputDir}/python`,
          ].join(" "), { cwd: path.join(__dirname, "../../eden") });
          execSync(`cp -r . ${outputDir}/python/eden`, {
            cwd: path.join(__dirname, "../../eden"),
          });
          return true;
        },
      },
      command: ["bash", "-c",
        "pip install -r requirements.txt -t /asset-output/python && cp -r . /asset-output/python/eden"
      ],
    },
  }),
  compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
  description: "eden common module layer",
});
```

- `eden/requirements.txt` に boto3, numpy, Pillow を記載
- `tryBundle()` が成功すれば Docker は使われない
- `--platform manylinux2014_x86_64 --only-binary=:all:` でLambda x86_64向けwheelを確実に取得
- Amplifyビルド環境(AL2023)でもローカルでもpipだけで完結

### Lambda 共通パターン

```python
# 各 handler.py の共通構造
import json
import os

def handler(event, context):
    # API Gateway proxy integration 形式
    body = json.loads(event.get("body", "{}"))
    
    # ... eden パッケージを使った処理 ...
    
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(result),
    }
```

## Tasks

- [ ] 1. ルートに `package.json` 作成 (Next.js + Amplify 依存)
- [ ] 2. `amplify/` の初期構成作成 (package.json, tsconfig.json, backend.ts)
- [ ] 3. `amplify/auth/resource.ts` 作成 (Cognito defineAuth)
- [ ] 4. `amplify/custom/api-stack.ts` 作成 (API Gateway + Lambda + SQS + DynamoDB + S3 の CDK定義)
- [ ] 5. `eden/requirements.txt` 作成 + `api-stack.ts` 内で `lambda.LayerVersion` + `bundling.local` (tryBundle) を定義
- [ ] 6. Lambda handler 骨格: upload_url, bbox_candidates
- [ ] 7. Lambda handler 骨格: register, register_worker
- [ ] 8. Lambda handler 骨格: job_status, search, add_info
- [ ] 9. Next.js App Router の骨格作成 (layout, page, amplify-config)
- [ ] 10. `amplify_outputs.json` から API エンドポイント読み取り + Cognito認証連携
- [ ] 11. kiro/dev にマージ＆プッシュ
