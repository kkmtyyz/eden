# Lambda Functions 一覧

## 同期 Lambda (API Gateway 経由)

### upload_url
- **エンドポイント**: `POST /upload-url`
- **役割**: クライアントが S3 に画像を直アップロードするための presigned PUT URL を発行
- **利用サービス**: S3 (署名生成)

### bbox_candidates
- **エンドポイント**: `POST /bbox-candidates`
- **役割**: 代表フレームに対して Nova Lite で BBox 検出を実行し、候補一覧と推奨インデックスを返却
- **利用サービス**: S3 (画像取得), Bedrock Nova Lite (BBox 検出)

### register
- **エンドポイント**: `POST /register`
- **役割**: 登録リクエストを受け付け、SQS にジョブを投入。job_id を即座に返却 (202)
- **利用サービス**: SQS (メッセージ送信), DynamoDB (ジョブ作成), Bedrock Guardrails (テキストガードレール)

### job_status
- **エンドポイント**: `GET /jobs/{id}`
- **役割**: ジョブの処理状態 (pending/processing/completed/failed) を返却
- **利用サービス**: DynamoDB (ジョブテーブル読み取り)

### search
- **エンドポイント**: `POST /search`
- **役割**: クエリ画像から物体検索を実行。BBox 検出 → クロップ → OCR → 埋め込み → ベクトル検索 → 3値判定 (緑/黄/赤)
- **利用サービス**: S3 (画像取得), Bedrock Nova Lite (BBox/OCR), Bedrock Titan (埋め込み), S3 Vectors (検索), DynamoDB (物体情報取得)

### add_info
- **エンドポイント**: `POST /objects/{id}/info`
- **役割**: 既存物体に情報テキストを追記 (upsert 方式)
- **利用サービス**: DynamoDB (物体テーブル更新), Bedrock Guardrails (テキストガードレール)

### download_url
- **エンドポイント**: `POST /download-url`
- **役割**: S3 上の画像 (サムネイル等) の presigned GET URL を発行
- **利用サービス**: S3 (署名生成)

## 非同期 Lambda (SQS trigger)

### register_worker
- **トリガー**: SQS (登録ジョブキュー, batchSize=1)
- **タイムアウト**: 15分
- **役割**: 物体登録のメイン処理を4フェーズで実行

| Phase | 処理内容 | 並列度 |
|-------|----------|--------|
| 1 | BBox 追跡 (全フレームで同一物体を追跡) | 4並列 |
| 2 | フレーム選定 (センタリング + 多様性で20枚選定) | — |
| 3 | クロップ → OCR → CLAHE → 4方向埋め込み | 4並列 |
| 4 | S3 Vectors 一括登録 + DynamoDB 更新 | — |

- **利用サービス**: S3 (画像取得/クロップ保存/サムネ保存), Bedrock Nova Lite (BBox/OCR), Bedrock Titan (埋め込み), Rekognition (ガードレール), S3 Vectors (ベクトル登録), DynamoDB (物体/ジョブ更新)

## 運用ツール Lambda

### reset_data
- **トリガー**: 手動 invoke のみ
- **役割**: S3 Vectors インデックス + DynamoDB (Objects/Jobs) を全削除
- **安全装置**: payload に `{"confirm": true}` が必須
- **利用サービス**: S3 Vectors (全削除), DynamoDB (全削除)
