"""ユーティリティ関数: ベクトルID構築・パース、DDBアイテム構築。"""

from __future__ import annotations

from typing import Any

# vector_id のセパレータ
_SEPARATOR = "##"


def build_vector_id(object_id: str, source_file: str, rotation: int) -> str:
    """DynamoDB用のベクトルIDを構築する。

    フォーマット: "{object_id}##{source_file}##{rotation}"

    Args:
        object_id: 物体ID (例: "aws_mug")
        source_file: 元画像ファイルパス
        rotation: 回転角度 (0, 90, 180, 270)

    Returns:
        構築されたvector_id文字列
    """
    return f"{object_id}{_SEPARATOR}{source_file}{_SEPARATOR}{rotation}"


def parse_vector_id(vector_id: str) -> dict[str, str]:
    """vector_idを解析してobject_id, source_file, rotationを取得する。

    Args:
        vector_id: build_vector_idで構築されたID文字列

    Returns:
        {"object_id": str, "source_file": str, "rotation": str}

    Raises:
        ValueError: フォーマットが不正な場合
    """
    parts = vector_id.split(_SEPARATOR)
    if len(parts) != 3:
        raise ValueError(
            f"Invalid vector_id format: '{vector_id}'. "
            f"Expected format: 'object_id{_SEPARATOR}source_file{_SEPARATOR}rotation'"
        )
    return {
        "object_id": parts[0],
        "source_file": parts[1],
        "rotation": parts[2],
    }


def build_ddb_vector_item(
    object_id: str,
    source_file: str,
    rotation: int,
    vector: list[float],
    text_tokens: list[str] | None = None,
) -> dict[str, Any]:
    """DynamoDB用のベクトルアイテムを構築する。

    text_tokensは非空リストの場合のみアイテムに含める
    (DynamoDBでは空リストの格納を避ける設計)。

    Args:
        object_id: 物体ID
        source_file: 元画像ファイルパス
        rotation: 回転角度
        vector: 埋め込みベクトル (float32のリスト)
        text_tokens: テキストトークンリスト (空リストやNoneの場合は除外)

    Returns:
        DynamoDB putItem用の辞書
    """
    vector_id = build_vector_id(object_id, source_file, rotation)

    item: dict[str, Any] = {
        "vector_id": {"S": vector_id},
        "object_id": {"S": object_id},
        "source_file": {"S": source_file},
        "rotation": {"N": str(rotation)},
        "vector": {"VF": vector},
    }

    # text_tokensは非空の場合のみ含める
    if text_tokens:
        item["text_tokens"] = {"SS": text_tokens}

    return item
