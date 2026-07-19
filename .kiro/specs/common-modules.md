# Spec: 共通モジュール化

experiments/ に散在するPoCロジックを、Lambda/ローカル両方から再利用可能な共通モジュールとして整理する。

## Requirements

### R1: モジュール構成
- `eden/` パッケージとして切り出し、`from eden.xxx import yyy` で利用可能にする
- experiments/ からの依存を切り離し、Lambda移植時にそのまま使える形にする
- boto3セッション/クライアント生成はモジュール内でハードコードせず、外部から注入可能にする（Lambda環境ではprofile不要）

### R2: 対象機能
以下のロジックをモジュール化する：
1. **BBox検出** — Nova LiteによるBBox検出 + repair_boxes パーサー
2. **クロップ** — BBox座標→パディング付きクロップ、中央物体選択ロジック
3. **文字抽出** — VLMによるOCR + NFKC正規化・トークン化
4. **画像埋め込み** — Titan Multimodal Embeddings (4方向回転対応)
5. **ベクトル検索** — S3 Vectors query + スコア計算 + 3値判定
6. **ガードレール** — Rekognitionによる3段チェック: detect_faces(顔検出→ポスター/フィギュア含めNG)、detect_labels(Person/HumanのInstances検出→拒否)、detect_moderation_labels(NSFW系→拒否)

### R3: 設計方針
- 各モジュールはステートレスな関数群とする（クラスでラップしない）
- AWS クライアントは引数で受け取る or デフォルトでboto3.client()を使う（profile_name依存を排除）
- 閾値・定数は `eden/config.py` に集約し、環境変数でオーバーライド可能にする
- 型ヒント（typing）を付ける
- Pillow Image オブジェクトを入出力の基本型とする（バイト列変換はユーティリティに閉じ込める）

### R4: 後方互換
- experiments/ のスクリプトは `from eden.xxx import yyy` に書き換え、動作が変わらないことを確認できる状態にする（実際のAWS呼び出しテストはローカルで行う想定）

## Design

### パッケージ構成

```
eden/
├── __init__.py
├── config.py          # 閾値・定数・環境変数読み込み
├── clients.py         # boto3クライアント生成ヘルパー
├── detection.py       # BBox検出、repair_boxes、_validate
├── crop.py            # クロップ関数、中央物体選択
├── ocr.py             # 文字抽出、normalize_text
├── embedding.py       # embed_image、回転付き複数埋め込み
├── search.py          # query_vectors、スコア計算、3値判定
├── guardrail.py       # Rekognitionガードレール（スタブ→実装）
└── image_util.py      # JPEG変換、EXIF transpose、リサイズ等
```

### モジュール間依存

```
image_util.py  ← 全モジュールから利用
config.py      ← 全モジュールから利用
clients.py     ← detection, ocr, embedding, search, guardrail から利用

detection.py   → image_util
crop.py        → detection, image_util
ocr.py         → image_util
embedding.py   → image_util
search.py      → ocr, embedding, crop, config
guardrail.py   → (独立)
```

### クライアント注入パターン

```python
# eden/clients.py
import boto3
from functools import lru_cache

def get_bedrock_client(region: str = "ap-northeast-1", session: boto3.Session | None = None):
    s = session or boto3.Session()
    return s.client("bedrock-runtime", region_name=region)
```

各関数は `client=None` パラメータを持ち、Noneならデフォルトクライアントを使用：
```python
# eden/detection.py
def detect_boxes(img: Image.Image, *, client=None) -> list[list[int]]:
    client = client or get_bedrock_client(region=config.BBOX_REGION)
    ...
```

### config.py 設計

```python
import os

# リージョン
BBOX_REGION = os.getenv("EDEN_BBOX_REGION", "ap-northeast-1")
EMBED_REGION = os.getenv("EDEN_EMBED_REGION", "us-east-1")
TEXT_REGION = os.getenv("EDEN_TEXT_REGION", "ap-northeast-1")
VECTOR_REGION = os.getenv("EDEN_VECTOR_REGION", "ap-northeast-1")

# モデル
BBOX_MODEL_ID = os.getenv("EDEN_BBOX_MODEL", "amazon.nova-lite-v1:0")
EMBED_MODEL_ID = os.getenv("EDEN_EMBED_MODEL", "amazon.titan-embed-image-v1")
TEXT_MODEL_ID = os.getenv("EDEN_TEXT_MODEL", "amazon.nova-lite-v1:0")

# ベクトル
EMBED_DIM = int(os.getenv("EDEN_EMBED_DIM", "1024"))
VECTOR_BUCKET = os.getenv("EDEN_VECTOR_BUCKET", "my-s3-vector-bucket")
VECTOR_INDEX = os.getenv("EDEN_VECTOR_INDEX", "my-s3-vector-index")

# 判定閾値
GREEN_SIM = float(os.getenv("EDEN_GREEN_SIM", "0.85"))
GREEN_MARGIN = float(os.getenv("EDEN_GREEN_MARGIN", "0.10"))
YELLOW_SIM = float(os.getenv("EDEN_YELLOW_SIM", "0.72"))
TEXT_BOOST = float(os.getenv("EDEN_TEXT_BOOST", "0.05"))
TEXT_MISMATCH_PENALTY = float(os.getenv("EDEN_TEXT_MISMATCH_PENALTY", "0.05"))
TEXT_MATCH_RATIO = float(os.getenv("EDEN_TEXT_MATCH_RATIO", "0.7"))

# 画像処理
MAX_IMAGE_SIZE = int(os.getenv("EDEN_MAX_IMAGE_SIZE", "1024"))
CROP_PAD = float(os.getenv("EDEN_CROP_PAD", "0.12"))
ROTATIONS = [0, 90, 180, 270]
TOP_K = int(os.getenv("EDEN_TOP_K", "20"))
```

### guardrail.py 設計

```python
# eden/guardrail.py
from dataclasses import dataclass

@dataclass
class GuardrailResult:
    passed: bool
    reason: str | None  # 拒否理由（passedがTrueならNone）
    details: list[str]  # 検出されたラベル/理由の一覧

def check_guardrail(image_bytes: bytes, *, client=None) -> GuardrailResult:
    """
    登録フローの最初に呼び出す。以下を順にチェック:
    
    1. detect_faces: 顔検出
       - 顔が1つでも検出されたら拒否
       - ポスターやフィギュアの顔も含めてNG判定
    2. detect_labels: 人物検出
       - ラベルが "Person" or "Human" かつ Instances が存在すれば拒否
    3. detect_moderation_labels: 不適切コンテンツ(NSFW系)検出
       - 信頼度 >= 70% のラベルが1つでもあれば拒否
    
    いずれか1つでも引っかかった時点で即拒否を返す（短絡評価）。
    
    Returns:
        GuardrailResult(passed=True/False, reason, details)
    """
```

- Rekognitionは東京リージョンを使用
- 画像バイト列を直接渡す（S3経由でない）
- Lambda実行時はIAMロールに rekognition:DetectFaces, rekognition:DetectLabels, rekognition:DetectModerationLabels を付与

## Tasks

- [ ] 1. `eden/__init__.py` と `eden/config.py` を作成（定数・閾値・環境変数オーバーライド）
- [ ] 2. `eden/image_util.py` を作成（jpeg_bytes, load_image, exif_transpose, resize）
- [ ] 3. `eden/clients.py` を作成（boto3クライアント生成ヘルパー）
- [ ] 4. `eden/detection.py` を作成（detect_boxes, repair_boxes, _validate）
- [ ] 5. `eden/crop.py` を作成（crop_bbox, select_center_object, crop_center_object）
- [ ] 6. `eden/ocr.py` を作成（extract_text, normalize_text）
- [ ] 7. `eden/embedding.py` を作成（embed_image, embed_rotations）
- [ ] 8. `eden/search.py` を作成（query_vectors, compute_scores, judge_verdict, search_object）
- [ ] 9. `eden/guardrail.py` を作成（detect_faces / detect_labels / detect_moderation_labels の3段チェック実装）
- [ ] 10. experiments/ のスクリプトを eden パッケージ利用に書き換え（動作確認はローカル）
- [ ] 11. プッシュ＆PR作成
