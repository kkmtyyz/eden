<!-- このファイルはKiroのsteering用ドキュメントです。開発時に参照してください。 -->
<!-- 最終更新: 2026-07-19 (kiro/dev ブランチの実装に基づく) -->

# 構成整理

## 全体アーキテクチャ

```
┌─ Amplifyプロジェクト(単一リポジトリ・全部入れ) ─────────────┐
│                                                              │
│  フロント: Next.js (App Router, 実質SPA、スマホ向け固定)     │
│    ├─ Amplify Hosting (HTTPS自動 → カメラAPI利用可)          │
│    └─ Tailwind CSS (ユーティリティファースト)                 │
│                                                              │
│  amplify/backend.ts (CDK)                                    │
│    ├─ auth: Cognito (defineAuth, 招待制)                     │
│    └─ カスタムスタック (backend.createStack)                  │
│        ├─ API Gateway REST + Cognitoオーソライザー            │
│        ├─ Lambda群 (Python 3.12, eden Layer)                 │
│        ├─ SQS (登録ジョブキュー, visibilityTimeout=15min)    │
│        ├─ DynamoDB (物体テーブル + ジョブテーブル)            │
│        ├─ S3 (画像バケット, CORS: PUT/GET/HEAD)              │
│        └─ S3 Vectors (CDK管理, eden-vec-{hash})              │
│                                                              │
│  addOutput({custom: {apiEndpoint}})                          │
│    → amplify_outputs.json 経由でフロントに自動注入            │
└──────────────────────────────────────────────┘
```

### 外部リソース (全て CDK 管理)
- **S3 Vectors**: 東京 (cosine / 1024次元 / float32)
  - バケット名: `eden-vec-{stackIdHash}` (sandbox/本番で衝突しない)
  - インデックス名: `eden-index`
  - filterable: object_id, text_tokens
  - non-filterable: label, source_file, rotation
- **Bedrock Nova Lite**: 東京 (BBox検出・文字抽出)
- **Bedrock Titan Multimodal**: us-east-1 (埋め込み, 東京未対応)
- **Rekognition**: 東京 (ガードレール)

## API・Lambda構成

| エンドポイント | 方式 | Lambda処理 |
|---|---|---|
| POST /upload-url | 同期 | presigned PUT URL 発行 (S3v4署名, リージョン明示) |
| POST /bbox-candidates | 同期 | 代表フレームにNova → BBox候補返却 |
| POST /register | 同期(202) | SQSへ投入、job_id返却。keys[], representative_key, box, initial_info を受付 |
| (SQS trigger) | 非同期 | ガードレール→追跡→クロップ→OCR→CLAHE光正規化→4方向埋め込み→S3 Vectors/DynamoDB登録 |
| GET /jobs/{id} | 同期 | ジョブ状態返却 (pending/processing/completed/failed) |
| POST /search | 同期 | BBox検出→上位10個をasyncio並列→3値判定→情報付き返却 |
| POST /objects/{id}/info | 同期 | DynamoDB info に追記 (upsert方式) |
| (手動invoke) reset_data | — | 運用ツール: S3 Vectorsインデックス+DynamoDBを全削除 |

### 設計判断
- Step Functions見送り (検索29秒内、登録SQS+Lambda15分で足りる)
- ECS見送り (ボトルネックはBedrock RPMクォータ → 引き上げ申請が先)
- API Gateway REST (Cognitoオーソライザーの相性が良い)

## データストア

### S3 (画像バケット)
- `uploads/` — 原画像 (presigned PUT でアップロード)
- `crops/{object_id}/` — 全クロップ画像 (再埋め込み原本)
- `thumbnails/{object_id}.jpg` — サムネイル (256x256)

### S3 Vectors
- dimension: 1024, cosine, float32
- filterable: `object_id`, `text_tokens` (空配列不可→無い時はキー省略)
- non-filterable: `label`, `source_file`, `rotation`
- ベクトルキー形式: `{object_id}__{filename}__{rotation_deg}`

### DynamoDB
- **物体テーブル**: PK=object_id, thumbnail_key, info: [{text, user_id, created_at}]
- **ジョブテーブル**: PK=job_id, status, object_id, created_at, error

## 判定ロジック (現在の値)

- sim = 1 − cosine distance
- 文字一致ブースト: +0.05 (部分一致 or 編集距離比率 >= 0.7)
- 文字不一致ペナルティ: −0.05 (両者に文字有り時のみ)
- margin = sim_raw ベースで top1 と別object_id最上位の差

### 3値判定
- **緑**: sim >= 0.80 かつ (margin >= 0.10 or 文字一致)
- **黄**: sim >= 0.72 かつ votes >= 2
- **赤**: それ以外 → 新規登録導線
- votes: top-k内のtop1_id出現数 (判定に使用)

## 画像処理パイプライン

### 前処理
- EXIF transpose → RGB変換 → thumbnail(1024, 1024)
- **CLAHE光正規化** (LAB L チャネル, clipLimit=2.0, tiles=8x8) — 埋め込み直前に適用

### クロップ
- BBox座標: 0-1000スケール
- **余白 (CROP_PAD): 6%** (各辺に対して)
- 検索時も登録時も同じ `crop_bbox()` を使用

### 埋め込み
- Titan Multimodal Embeddings (us-east-1)
- 4方向回転 (0, 90, 180, 270)
- CLAHE 適用後の画像を入力

## デプロイ

### ブランチ構成
- `kiro/dev` — 開発統合ブランチ (sandbox + Amplify Hosting 連携)
- `main` — 安定版

### Amplify Hosting
- GitHub 連携でブランチデプロイ (push で自動ビルド)
- `amplify.yml` で制御:
  - ルート: `npm ci`
  - amplify/: `npm install` (transitive 依存の不整合回避)
  - `npx ampx pipeline-deploy`

### sandbox (開発用)
- `npx ampx sandbox` でローカル開発
- sandbox と本番で S3 Vectors バケット名が衝突しない設計

## 運用ツール

### データリセット Lambda (reset_data)
- 用途: S3 Vectors インデックス + DynamoDB (Objects/Jobs) を全削除
- 安全装置: `{"confirm": true}` を payload に渡さないと実行しない
- 実行方法: `aws lambda invoke --function-name <FN> --payload '{"confirm":true}' out.json`
