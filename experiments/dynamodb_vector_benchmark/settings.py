"""設定ファイル読み込みモジュール。

config.yaml から設定を読み込み、各モジュールから参照できるようにする。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# config.yaml のパス
_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_config: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    """config.yaml を読み込む。"""
    global _config
    if _config is None:
        if not _CONFIG_PATH.exists():
            raise FileNotFoundError(
                f"設定ファイルが見つかりません: {_CONFIG_PATH}\n"
                f"config.yaml を作成してください。"
            )
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
    return _config


def get(section: str, key: str, default: Any = None) -> Any:
    """設定値を取得する。

    Args:
        section: セクション名 (例: "s3_vectors", "dynamodb_vector")
        key: キー名 (例: "bucket", "table")
        default: デフォルト値

    Returns:
        設定値
    """
    cfg = _load_config()
    return cfg.get(section, {}).get(key, default)


def get_section(section: str) -> dict[str, Any]:
    """セクション全体を取得する。"""
    cfg = _load_config()
    return cfg.get(section, {})


# ─── 便利アクセサ ─────────────────────────────────────────────

def aws_profile() -> str | None:
    return get("aws", "profile")

def aws_region() -> str:
    return get("aws", "region", "ap-northeast-1")

# S3 Vectors
def s3v_bucket() -> str:
    return get("s3_vectors", "bucket", "")

def s3v_index() -> str:
    return get("s3_vectors", "index", "eden-index")

def s3v_region() -> str:
    return get("s3_vectors", "region", "ap-northeast-1")

# DynamoDB Vector Search
def ddb_table() -> str:
    return get("dynamodb_vector", "table", "eden-vector-benchmark")

def ddb_index() -> str:
    return get("dynamodb_vector", "index", "eden-vector-index")

def ddb_region() -> str:
    return get("dynamodb_vector", "region", "ap-northeast-1")

# DynamoDB Objects (移行元)
def objects_table() -> str:
    return get("dynamodb_objects", "table", "eden-objects")

def objects_region() -> str:
    return get("dynamodb_objects", "region", "ap-northeast-1")

# S3 Images
def images_bucket() -> str:
    return get("s3_images", "bucket", "")

# Bedrock
def bbox_region() -> str:
    return get("bedrock", "bbox_region", "ap-northeast-1")

def embed_region() -> str:
    return get("bedrock", "embed_region", "us-east-1")

def text_region() -> str:
    return get("bedrock", "text_region", "ap-northeast-1")

def bbox_model() -> str:
    return get("bedrock", "bbox_model", "amazon.nova-lite-v1:0")

def embed_model() -> str:
    return get("bedrock", "embed_model", "amazon.titan-embed-image-v1")

def text_model() -> str:
    return get("bedrock", "text_model", "amazon.nova-lite-v1:0")

# Benchmark
def top_k() -> int:
    return get("benchmark", "top_k", 20)

def embed_dim() -> int:
    return get("benchmark", "embed_dim", 1024)

def iterations() -> int:
    return get("benchmark", "iterations", 10)

def warmup() -> bool:
    return get("benchmark", "warmup", True)
