<!-- このファイルはKiroのsteering用ドキュメントです。開発時に参照してください。 -->
<!-- 最終更新: 2026-08-05 (kiro/dev ブランチの実装に基づく) -->

# 機能別整理

## 物体登録フロー

### クライアント (スマホWebアプリ)
1. 登録画面 → カメラ起動、「物体の周りをゆっくり動かしてください」ガイド枠表示
2. 「撮影開始」ボタン → 約100msごとにフレームキャプチャ (表示範囲=キャプチャ範囲)
3. 「撮影完了」ボタン → プルーニング処理:
   - **ブレ検出** (Laplacian variance < 50 のフレームを除外)
   - **類似フレーム除去** (NCC 正規化相関 > 0.97 を除外)
   - **等間隔サンプリング** (上限50枚) ← バックエンドで20枚に絞る
4. プルーニング後のフレーム一覧表示 → ユーザーが**代表画像(サムネ用)を1枚選択**
5. 全フレームを presigned PUT で S3 に直アップロード (3並列)
6. 代表画像で POST /bbox-candidates → BBox 自動選択 (default_index)
7. **物体情報入力** (名前・説明テキスト) — 検索ヒット時に表示される初期情報
   - テキストガードレール (Bedrock Guardrails) でチェック
   - NG時: エラーメッセージ表示、情報入力画面に戻してやり直し (画像再アップ不要)
8. POST /register (keys[], representative_key, box, object_id, initial_info) → job_id 取得
9. GET /jobs/{id} を3秒ポーリング → 完了表示
   - Rekognition NG時: 日本語メッセージで表示 (「この画像は登録できません（人物の顔が含まれています）」等)

### バックエンド (register_worker, SQS trigger) — 4並列処理
10. 代表フレームのクロップでガードレール (Rekognition):
    - detect_faces / detect_labels(Person) / detect_moderation_labels → 該当なら登録拒否
11. **Phase 1: BBox追跡 (4並列, ThreadPoolExecutor)**
    - 全フレーム (最大50枚) に対してBBox検出 (tool use) → IoU >= 0.3 で同一物体判定
    - 追跡失敗フレームを除外
12. **Phase 2: フレーム選定 (20枚)**
    - センタリングスコア: BBox中心が画像中心に近いほど高スコア
    - 多様性: BBox位置が近すぎるフレームを間引き
    - 代表フレームは必ず含む
13. **Phase 3: OCR + 埋め込み (4並列, ThreadPoolExecutor)**
    - BBoxクロップ (EXIF transpose済み、余白6%)
    - クロップ画像を S3 に保存 (再埋め込み原本)
    - 代表フレームのクロップをサムネイル (256x256) として保存 (**新規登録時のみ、追加登録時はサムネ維持**)
    - VLM(Nova Lite)でクロップ後画像から文字抽出 → NFKC正規化・小文字化・トークン化
    - **CLAHE光正規化** → 4方向回転 → Titan Multimodal Embeddings で1024次元ベクトル化
14. **Phase 4: 登録**
    - S3 Vectors に一括登録 (100件バッチ):
      - filterable: object_id, text_tokens (空なしならキー省略)
      - non-filterable: label, source_file, rotation
    - DynamoDB に物体レコード (object_id, thumbnail_key, info[initial_info])
    - ジョブステータス更新 → completed

### ベクトル数の目安
- 20フレーム × 4方向回転 = 最大80ベクトル/物体
- 50枚アップロード → バックエンドで20枚選定 → 追跡失敗を除外済みなので実際は60〜80程度

### 処理時間の目安
- Phase 1 (BBox追跡 50枚 × 4並列): ~20秒
- Phase 2 (フレーム選定): ~0秒 (CPU計算のみ)
- Phase 3 (OCR+埋め込み 20枚 × 4並列): ~25秒
- Phase 4 (S3 Vectors登録): ~3秒
- 合計: ~50秒 (並列化前: ~90秒)

## 物体検索フロー

### バックエンド (search Lambda)
1. クエリ画像を S3 から取得 → EXIF transpose → リサイズ (1024x1024)
2. Nova Lite で BBox 検出 (tool use, constrained decoding) → 中央×面積スコアで上位**10個**を選定
3. 各 BBox を asyncio 並列で処理:
   - crop_bbox (余白6%) でクロップ
   - VLM で文字抽出 (読めなければ空)
   - **CLAHE光正規化** → Titan でベクトル化 → S3 Vectors で top-20 検索
4. スコア計算 (BBoxごと):
   - cosine distance → sim (=1−distance), sim_raw を保持
   - 文字一致ブースト +0.05 / 文字不一致ペナルティ −0.05
   - margin: sim_raw ベースで top1 と別object_id最上位の差
   - votes: top-k 内の top1_id 出現数
5. 3値判定:
   - 緑: sim >= 0.80 かつ (margin >= 0.10 or 文字一致)
   - 黄: sim >= 0.72 かつ votes >= 2
   - 赤: それ以外
6. object_id で重複排除 → DynamoDB から情報+サムネ取得 → レスポンス
   - **ゴーストベクトル防御**: DynamoDB にレコードがない場合は red に降格

### クライアント
7. BBox オーバーレイ表示 (0-1000 スケール → CSS % 変換):
   - 緑: サムネイル表示 + 情報閲覧 + 情報追加フォーム
   - 黄: サムネイル表示 + 「この物体?」Yes/No (投票機能付き)
   - 赤: 新規登録導線 (/register へ遷移)

## 黄色確認UI (投票機能)

- 「はい」ボタン押下時:
  1. registerObject(imageKey, box, object_id) で既存物体にベクトル追加登録
  2. SQS → register_worker がベクトル追加 (1枚×4回転 = 最大4ベクトル)
  3. ジョブポーリングで進捗表示 (登録中→処理中→完了/失敗)
  4. 追加登録時はサムネイルを上書きしない (登録時のユーザー選択を維持)
- 「いいえ」ボタン: テキスト表示のみ (将来拡張予定)

## 物体情報追加フロー

1. 緑BBoxタップ → 既存情報一覧表示 → 追加入力
2. テキストガードレール (Bedrock Guardrails) でチェック
   - NG時: フォーム下に赤文字で「不適切な表現が含まれています」表示
3. POST /objects/{id}/info → DynamoDB info リストに追記 (upsert方式)
   - レコードが存在しなくても自動作成
   - {text, user_id(Cognito sub), created_at}
4. 以降の検索でオーバーレイ表示に反映

## サムネイル表示フロー

- 緑/黄パネル表示時に thumbnail_key があれば:
  1. POST /download-url に key を送信 → presigned GET URL 取得
  2. URL で `<img>` タグに画像表示
  3. ローディング中はプレースホルダー、エラー時はサイレントフォールバック

## フロントエンド構成

### 技術スタック
- Next.js 14 (App Router)
- Tailwind CSS 3.4
- aws-amplify 6 + @aws-amplify/ui-react 6

### ページ構成
- `/` — 検索画面 (SearchFlow コンポーネント)
- `/register` — 登録画面 (RegisterFlow コンポーネント)

### 主要コンポーネント
- `camera.tsx` — リアカメラ起動、ガイド枠、captureFrame() (表示範囲のみキャプチャ)
- `search-flow.tsx` — 撮影→アップロード→検索→結果表示の統合フロー (imageKey をSearchResultに伝搬)
- `search-result.tsx` — BBoxオーバーレイ(緑/黄/赤)、詳細パネル、情報追加、サムネイル表示、投票機能
- `register-flow.tsx` — 撮影→プルーニング→フレーム選択→アップロード→情報入力→登録 (ガードレールNGでinfo-inputに戻す)
- `frame-selector.tsx` — プルーニング後フレーム一覧、代表画像選択
- `info-input.tsx` — 登録時の初期物体情報入力 (errorMessage prop でエラー表示)
- `job-progress.tsx` — ジョブポーリング(3秒間隔)、経過時間表示

### UI設定 (app/lib/ui-config.ts)
- BBOX_BORDER_WIDTH: 4px
- カメラ領域: max-h-[70dvh]

### デバッグログ (console.log)
全コンポーネント・ライブラリにプレフィックス付きログ:
- `[camera]` / `[upload]` / `[api]` / `[search-flow]` / `[register-flow]` / `[frame-capture]` / `[bbox-selector]` / `[job-progress]` / `[info-input]`

## eden 共通パッケージ (Python)

### モジュール構成
```
eden/
├── __init__.py
├── config.py          # 閾値・定数・環境変数オーバーライド
├── clients.py         # boto3クライアント生成ヘルパー
├── detection.py       # BBox検出 (Nova Lite, tool use + フォールバック)
├── crop.py            # クロップ (余白6%)、中央物体選択
├── ocr.py             # 文字抽出 (Nova Lite)、正規化
├── embedding.py       # Titan Multimodal (4方向回転、CLAHE前処理付き)
├── search.py          # S3 Vectors query、スコア計算、3値判定
├── guardrail.py       # Rekognition (顔/人物/NSFW)
├── text_guardrail.py  # Bedrock Guardrails (ApplyGuardrail API, Standard Tier)
├── image_util.py      # JPEG変換、EXIF、CLAHE光正規化
└── requirements.txt   # boto3, numpy, Pillow
```

### 主要設定値 (eden/config.py)
| 設定 | デフォルト | 環境変数 |
|---|---|---|
| GREEN_SIM | 0.80 | EDEN_GREEN_SIM |
| GREEN_MARGIN | 0.10 | EDEN_GREEN_MARGIN |
| YELLOW_SIM | 0.72 | EDEN_YELLOW_SIM |
| CROP_PAD | 0.06 | EDEN_CROP_PAD |
| MAX_IMAGE_SIZE | 1024 | EDEN_MAX_IMAGE_SIZE |
| EMBED_DIM | 1024 | EDEN_EMBED_DIM |
| TOP_K | 20 | EDEN_TOP_K |
| ROTATIONS | [0,90,180,270] | — |
| TEXT_GUARDRAIL_ID | (空) | EDEN_TEXT_GUARDRAIL_ID |
| TEXT_GUARDRAIL_VERSION | 1 | EDEN_TEXT_GUARDRAIL_VERSION |
| TEXT_GUARDRAIL_REGION | ap-northeast-1 | EDEN_TEXT_GUARDRAIL_REGION |

## モデル乗り換え運用 (blue/green)

1. 新インデックス作成 (次元は新モデルに合わせる)
2. S3保存済みクロップからバッチ再埋め込み → 新インデックスへ
3. 評価データセットで 07_evaluate.py を回して閾値再キャリブレーション
4. 参照先切り替え → 旧インデックス削除

## 今後の精度改善候補 (未実装)

- 検索時にクエリ画像を4方向回転して検索 (角度ミスマッチ軽減)
- 全体 + 中央50%クロップの2パターン登録 (部分一致対応)
- 4分割ベクトル化 (見切れ対応)
- BBox選択のUI復活 (複数候補から選ばせる)
- Nova Pro への切り替え (コスト13倍のため保留)
- フレーム選定にブレスコア (Laplacian variance) も加味する (現在はセンタリング+多様性のみ)
- DynamoDB Vector Search への移行 (ベンチマーク完了、P50 -26%速い、精度100%一致)
