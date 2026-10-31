<!-- このファイルはKiroのsteering用ドキュメントです。開発時に参照してください。 -->
<!-- 最終更新: 2026-08-06 (kiro/dev ブランチの実装に基づく, DynamoDB Vector Search 移行完了) -->

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
│        ├─ DynamoDB VectorsTable (CDK外管理, Lambda で作成)    │
│        ├─ S3 (画像バケット, CORS: PUT/GET/HEAD)              │
│        ├─ S3 Vectors (移行完了後削除予定, RETAIN)            │
│        └─ Bedrock Guardrails (テキストガードレール, CDK管理)  │
│                                                              │
│  addOutput({custom: {apiEndpoint}})                          │
│    → amplify_outputs.json 経由でフロントに自動注入            │
└──────────────────────────────────────────────┘
```

### 外部リソース
- **DynamoDB VectorsTable**: 東京 (ベクトル検索)
  - テーブル名: `eden-vectors-{stackIdShortHash}` (CDK で名前のみ定義、テーブル自体は migrate_vectors Lambda で作成)
  - PK: vector_id (String)。形式: `{object_id}##{source_file}##{rotation}`
  - VectorIndex: eden-vector-index (1024次元, COSINE, Projection=ALL)
  - 属性: vector_id, object_id, source_file, rotation, vector (L of N), text_tokens (SS, optional)
  - **CloudFormation/CDK は VectorIndexes プロパティ未対応**のため、テーブル作成は DynamoDB API (create_table) で直接行う
- **S3 Vectors** (移行完了後に削除予定): 東京 (cosine / 1024次元 / float32)
  - バケット名: `eden-vec-{stackIdShortHash}`
  - インデックス名: `eden-index`
  - CDK で CfnVectorBucket/CfnIndex として定義 (RemovalPolicy.RETAIN)
  - 移行 Lambda がデータを読み取るために維持。完全移行確認後に手動削除する
- **Bedrock Nova Lite**: 東京 (BBox検出・文字抽出)
  - BBox検出: tool use (constrained decoding) で構造化出力
  - フォールバック: プロンプトベース + repair_boxes パーサー
- **Bedrock Titan Multimodal**: us-east-1 (埋め込み, 東京未対応)
- **Rekognition**: 東京 (画像ガードレール: 顔/人物/NSFW)
- **Bedrock Guardrails**: 東京 (テキストガードレール: コメントモデレーション)
  - Standard Tier (日本語対応, クロスリージョン推論)
  - ApplyGuardrail API (スタンドアロン、モデル呼び出し不要)
  - CDK CfnGuardrail で管理

## API・Lambda構成

| エンドポイント | 方式 | Lambda処理 |
|---|---|---|
| POST /upload-url | 同期 | presigned PUT URL 発行 (S3v4署名, リージョン明示) |
| POST /download-url | 同期 | presigned GET URL 発行 (サムネイル/画像取得用) |
| POST /bbox-candidates | 同期 | 代表フレームにNova (tool use) → BBox候補返却 |
| POST /register | 同期(202) | テキストガードレール → SQSへ投入、job_id返却 |
| (SQS trigger) | 非同期 | ガードレール→追跡(4並列)→選定→OCR+埋め込み(4並列)→DynamoDB VectorsTable登録 |
| GET /jobs/{id} | 同期 | ジョブ状態返却 (pending/processing/completed/failed) |
| POST /search | 同期 | BBox検出(tool use)→上位10個をasyncio並列→DynamoDB SearchVectors→3値判定→ゴースト防御→情報付き返却 |
| POST /objects/{id}/info | 同期 | テキストガードレール → DynamoDB info に追記 (upsert方式) |
| (手動invoke) reset_data | — | 運用ツール: S3 Vectors + DynamoDB VectorsTable + Objects/Jobs を全削除 |
| (手動invoke) reset_data_ddb | — | 運用ツール: DynamoDB VectorsTable + Objects/Jobs を全削除 (S3 Vectors は残す) |
| (手動invoke) migrate_vectors | — | 運用ツール: ベクトル移行 (3モード: restore/migrate/create-table) |

### 設計判断
- Step Functions見送り (検索29秒内、登録SQS+Lambda15分で足りる)
- ECS見送り (ボトルネックはBedrock RPMクォータ → 引き上げ申請が先)
- API Gateway REST (Cognitoオーソライザーの相性が良い)
- BBox検出: tool use (constrained decoding) を使用。フォールバックとしてプロンプトベース+JSONパーサー
- DynamoDB Vector Search: S3 Vectors から移行 (P50 -26%、精度100%一致のベンチマーク結果に基づく)
- VectorsTable は CDK/CloudFormation 外で管理 (VectorIndexes 未対応のため Lambda で create_table API を直接呼出)

## データストア

### S3 (画像バケット)
- `uploads/` — 原画像 (presigned PUT でアップロード)
- `crops/{object_id}/` — 全クロップ画像 (再埋め込み原本)
- `thumbnails/{object_id}.jpg` — サムネイル (256x256)

### DynamoDB VectorsTable
- PK: vector_id (String)。形式: `{object_id}##{source_file}##{rotation}`
- VectorIndex: eden-vector-index (1024次元, COSINE距離, Projection=ALL)
- 属性:
  - vector_id (S): パーティションキー
  - object_id (S): 物体識別子
  - source_file (S): 元画像ファイルパス
  - rotation (N): 回転角度 (0, 90, 180, 270)
  - vector (L of N): 1024次元埋め込みベクトル
  - text_tokens (SS): OCRトークン (空の場合は属性省略)
- 書き込み: BatchWriteItem (25件バッチ)、UnprocessedItems リトライ
- 検索: search_vectors API (SearchVector=[{"N": str(v)}], TopK=20)
- 削除: Scan + BatchWriteItem DeleteRequest (25件バッチ)

### S3 Vectors (移行完了後削除予定)
- dimension: 1024, cosine, float32
- バケット名: `eden-vec-{stackIdShortHash}`
- インデックス名: `eden-index`
- CDK で RETAIN ポリシーで定義中 (migrate_vectors Lambda がデータを読み取るために維持)

### DynamoDB (その他)
- **物体テーブル**: PK=object_id, thumbnail_key, info: [{text, user_id, created_at}]
- **ジョブテーブル**: PK=job_id, status, object_id, created_at, error

## ガードレール

### 画像ガードレール (Rekognition)
- 登録時のみ (register_worker, 代表フレームのクロップで実施)
- detect_faces / detect_labels(Person) / detect_moderation_labels
- 該当時: ジョブ failed、フロントに日本語エラーメッセージ

### テキストガードレール (Bedrock Guardrails)
- 2箇所: register Lambda (initial_info) + add_info Lambda (text)
- ApplyGuardrail API (スタンドアロン、モデル呼び出し不要)
- Standard Tier + クロスリージョン推論 (APACプロファイル)
- CDK CfnGuardrail で管理 (デプロイ時自動作成)
- コンテンツフィルター: HATE/INSULTS/SEXUAL/VIOLENCE/MISCONDUCT
- NG時: 400エラー → フロントでインラインエラー表示 (やり直し可)
- 未設定時 (GUARDRAIL_ID空): スキップ (可用性優先)
- API呼び出し失敗時: パスさせる (可用性優先)

## 判定ロジック (現在の値)

- sim = 1 − cosine distance (Score)
- 文字一致ブースト: +0.05 (部分一致 or 編集距離比率 >= 0.7)
- 文字不一致ペナルティ: −0.05 (両者に文字有り時のみ)
- margin = sim_raw ベースで top1 と別object_id最上位の差

### 3値判定
- **緑**: sim >= 0.80 かつ (margin >= 0.10 or 文字一致)
- **黄**: sim >= 0.72 かつ votes >= 2
- **赤**: それ以外 → 新規登録導線
- votes: top-k内のtop1_id出現数 (判定に使用)

### ゴーストベクトル防御 (search Lambda)
- VectorsTable にベクトルが残っていても Objects テーブルにレコードがなければ red に降格
- リセット後のゴースト表示を防止

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

### BBox検出 (detection.py)
- **主方式**: tool use (constrained decoding)
  - DetectObjects ツールスキーマで `boxes: [[int, int, int, int], ...]` を強制
  - toolChoice で強制呼び出し
  - 不正JSON出力が原理的に発生しない
- **フォールバック**: プロンプトベース + repair_boxes パーサー
  - JSONパース → 正規表現 → フラット数列救済 の3段階

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

### 初回デプロイ手順 (DynamoDB VectorsTable)
1. Amplify デプロイ (Lambda 関数が作成される)
2. `migrate_vectors` Lambda を invoke:
   - テーブル作成 + S3V からデータ移行: `{"confirm": true, "mode": "migrate"}`
   - テーブル作成のみ: `{"confirm": true, "mode": "create-table"}`

## 運用ツール

### データリセット Lambda (reset_data)
- 用途: S3 Vectors + DynamoDB VectorsTable + Objects/Jobs テーブルを全削除
- S3 バケット内のファイル (uploads/, crops/, thumbnails/) は削除しない
- 安全装置: `{"confirm": true}` を payload に渡さないと実行しない
- 実行方法: `aws lambda invoke --function-name <FN> --payload '{"confirm":true}' out.json`

### DynamoDB版リセット Lambda (reset_data_ddb)
- 用途: DynamoDB VectorsTable + Objects/Jobs テーブルを全削除 (S3 Vectors は残す)
- 安全装置: `{"confirm": true}` を payload に渡さないと実行しない

### ベクトル移行 Lambda (migrate_vectors)
- 3つのモードをサポート:
  - `"mode": "restore"` — DynamoDB → S3 Vectors へベクトル復元
    - `source_table` パラメータで読み込み元テーブルを指定 (デフォルト: eden-vector-benchmark)
  - `"mode": "migrate"` — S3 Vectors → DynamoDB VectorsTable へ移行 (テーブル作成込み)
  - `"mode": "create-table"` — DynamoDB VectorsTable の作成のみ
- 安全装置: `{"confirm": true}` を payload に渡さないと実行しない
- タイムアウト: 15分、メモリ: 1024MB
- 実行例:
  ```bash
  # S3V復元 (DDB→S3V)
  aws lambda invoke --function-name <FN> --payload '{"confirm":true,"mode":"restore","source_table":"eden-vector-benchmark"}' out.json
  # DDB移行 (S3V→DDB, テーブル作成込み)
  aws lambda invoke --function-name <FN> --payload '{"confirm":true,"mode":"migrate"}' out.json
  # テーブル作成のみ
  aws lambda invoke --function-name <FN> --payload '{"confirm":true,"mode":"create-table"}' out.json
  ```
