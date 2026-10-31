"""設定値の集約モジュール。環境変数でオーバーライド可能。"""

import os

# ─── リージョン ───────────────────────────────────────────────
BBOX_REGION: str = os.getenv("EDEN_BBOX_REGION", "ap-northeast-1")
EMBED_REGION: str = os.getenv("EDEN_EMBED_REGION", "us-east-1")
TEXT_REGION: str = os.getenv("EDEN_TEXT_REGION", "ap-northeast-1")
VECTOR_REGION: str = os.getenv("EDEN_VECTOR_REGION", "ap-northeast-1")
REKOGNITION_REGION: str = os.getenv("EDEN_REKOGNITION_REGION", "ap-northeast-1")

# ─── モデル ───────────────────────────────────────────────────
BBOX_MODEL_ID: str = os.getenv("EDEN_BBOX_MODEL", "amazon.nova-lite-v1:0")
EMBED_MODEL_ID: str = os.getenv("EDEN_EMBED_MODEL", "amazon.titan-embed-image-v1")
TEXT_MODEL_ID: str = os.getenv("EDEN_TEXT_MODEL", "amazon.nova-lite-v1:0")

# ─── ベクトル ─────────────────────────────────────────────────
EMBED_DIM: int = int(os.getenv("EDEN_EMBED_DIM", "1024"))
VECTORS_TABLE: str = os.getenv("EDEN_VECTORS_TABLE", "eden-vectors")

# [S3V_DISABLED] DynamoDB Vector Search に移行 (2026-08-05)
# 将来ベクトルストアを切り替える場合に再利用可能
# ---
# VECTOR_BUCKET: str = os.getenv("EDEN_VECTOR_BUCKET", "my-s3-vector-bucket")
# VECTOR_INDEX: str = os.getenv("EDEN_VECTOR_INDEX", "my-s3-vector-index")
# ---

# ─── 判定閾値 ─────────────────────────────────────────────────
GREEN_SIM: float = float(os.getenv("EDEN_GREEN_SIM", "0.80"))
GREEN_MARGIN: float = float(os.getenv("EDEN_GREEN_MARGIN", "0.10"))
YELLOW_SIM: float = float(os.getenv("EDEN_YELLOW_SIM", "0.72"))
TEXT_BOOST: float = float(os.getenv("EDEN_TEXT_BOOST", "0.05"))
TEXT_MISMATCH_PENALTY: float = float(os.getenv("EDEN_TEXT_MISMATCH_PENALTY", "0.05"))
TEXT_MATCH_RATIO: float = float(os.getenv("EDEN_TEXT_MATCH_RATIO", "0.7"))

# ─── 画像処理 ─────────────────────────────────────────────────
MAX_IMAGE_SIZE: int = int(os.getenv("EDEN_MAX_IMAGE_SIZE", "1024"))
CROP_PAD: float = float(os.getenv("EDEN_CROP_PAD", "0.06"))
ROTATIONS: list[int] = [0, 90, 180, 270]
TOP_K: int = int(os.getenv("EDEN_TOP_K", "20"))

# ─── ガードレール (Rekognition) ───────────────────────────────
GUARDRAIL_MODERATION_MIN_CONFIDENCE: float = float(
    os.getenv("EDEN_GUARDRAIL_MODERATION_MIN_CONFIDENCE", "70.0")
)

# ─── テキストガードレール (Bedrock Guardrails) ─────────────────
# Standard Tier 使用 (日本語対応にはクロスリージョン推論の有効化が必要)
TEXT_GUARDRAIL_ID: str = os.getenv("EDEN_TEXT_GUARDRAIL_ID", "")
TEXT_GUARDRAIL_VERSION: str = os.getenv("EDEN_TEXT_GUARDRAIL_VERSION", "1")
TEXT_GUARDRAIL_REGION: str = os.getenv("EDEN_TEXT_GUARDRAIL_REGION", "ap-northeast-1")
