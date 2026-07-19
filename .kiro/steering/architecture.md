<!-- このファイルはKiroのsteering用ドキュメントです。開発時に参照してください。 -->

# 構成整理
## 全体アーキテクチャ
┌─ Amplifyプロジェクト(単一リポジトリ・全部入れ) ─────────────┐
│                                                              │
│  フロント: Next.js (実質SPA、スマホ向け固定)                  │
│    └─ Amplify Hosting (HTTPS自動 → カメラAPI利用可)          │
│                                                              │
│  amplify/backend.ts (CDK)                                    │
│    ├─ auth: Cognito (defineAuth)                             │
│    └─ カスタムスタック (backend.createStack)                  │
│        ├─ API Gateway REST + Cognitoオーソライザー            │
│        │    (backend.auth.resources.userPool を参照渡し)      │
│        ├─ Lambda群 (Pythonランタイム、PoCコード移植)          │
│        ├─ SQS (登録ジョブキュー)                              │
│        ├─ DynamoDB                                           │
│        └─ S3 (画像バケット)                                   │
│                                                              │
│  addOutput({custom: {apiEndpoint}})                          │
│    → amplify_outputs.json 経由でフロントに自動注入            │
└──────────────────────────────────────────────┘
- 外部リソース:
  - S3 Vectors      : 東京 (cosine / 1024次元 / filterable: object_id, text_tokens)
  - Bedrock Nova    : 東京 (BBox検出・文字抽出)
  - Bedrock Titan   : us-east-1 (Multimodal Embeddings、東京未対応)
  - Rekognition     : 東京 (ガードレール)

## API・Lambda構成
- エンドポイント 方式 Lambda処理
  - POST /upload-url
    - 同期
    - presigned発行
    - S3直PUT用URL発行
  - POST /bbox-candidates
    - 同期
    - BBox候補
    - 代表フレームにNova → 候補返却(中央×面積スコアでデフォルト提示)
  - POST /register
    - 同期(202)
    - ジョブ投入
    - SQSへ投入、job_idを返す
  - (SQS trigger)
    - 非同期
    - 登録処理ガードレール → 追跡 → クロップS3保存 → 文字抽出 → 4方向埋め込み → S3 Vectors/DynamoDB登録。状態はDynamoDBへ
  - GET /jobs/{id}
    - 同期
    - ジョブ状態
    - フロントからポーリング
  - POST /search
    - 同期
    - 検索
    - BBox検出 → 上位5個をasyncio並列(クロップ・文字抽出・埋め込み・query_vectors) → 3値判定 → 情報付きで返却
  - POST /objects/{id}/info
    - 同期
    - 情報追加DynamoDB info に追記
- Step Functionsは導入見送り(検索は同期で29秒内、登録はSQS+Lambda 15分で足りる)。本番化でリトライ制御・可視化が欲しくなったら登録パイプラインだけ昇格
- ECSも見送り。スケールボトルネックはBedrock RPMクォータ → 増えたら引き上げ申請が先

## データストア
- S3(画像バケット)
  - 原画像(ユーザーアップロード、presigned PUT)
  - 全クロップ画像 = モデル乗り換え時の再埋め込み原本
  - サムネイル(presigned GETで黄色確認UI・オーバーレイに使用)
- S3 Vectors
  - dimension 1024 / cosine / float32
  - filterable: object_id, text_tokens(空配列不可→無い時はキー省略)
  - non-filterable: label, source_file
- DynamoDB
  - 物体テーブル: PK=object_id, thumbnail_key, info: [{text, user_id, created_at}]
  - ジョブテーブル: PK=job_id, status(登録の非同期処理用)

## 判定ロジック(PoC確定値)
- sim = 1 − cosine distance、文字一致 +0.05 / 文字不一致(両者に文字有り時のみ) −0.05
- margin = sim_raw ベースでtop1と別object_id最上位の差
- 緑: sim >= 0.85 かつ (margin >= 0.10 or 文字一致)
- 黄: sim >= 0.72 → サムネ提示でユーザー確認
- 赤: それ以外 → 新規登録導線
- votes: 実装保持・判定未使用(物体数増で再評価)

## 開発の進め方
- PoCのPythonコード(クロップ・埋め込み・検索・判定)を共通モジュール化 → ローカルで最終確認
- npx ampx sandbox でauth+カスタムスタック構築(構成変更はCFn更新で数分かかるので、ロジックはローカルで固めてから載せる)
- フロント(カメラ → フレームキャプチャ → プルーニング → presigned PUT)
- 検索フロー結合 → 登録フロー結合 → 情報追加
- ブランチデプロイで環境分離(Gen2の恩恵でタダ)

