"""DynamoDB Vector Search クエリ関数群。

S3 Vectors の query_vectors と互換性のあるレスポンス形式に正規化し、
既存の eden.search.compute_scores / judge_verdict を再利用可能にする。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import boto3
from PIL import Image

# eden パッケージへのパス追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eden import config
from eden.clients import get_bedrock_embed_client, get_bedrock_text_client
from eden.crop import crop_center_object
from eden.embedding import embed_image
from eden.ocr import extract_text, normalize_text
from eden.search import SearchResult, compute_scores, judge_verdict

from .utils import parse_vector_id

# DynamoDB Vector Search のデフォルト設定
DDB_TABLE_NAME: str = "eden-vector-benchmark"
DDB_INDEX_NAME: str = "eden-vector-index"


def _normalize_ddb_response(ddb_hits: list[dict[str, Any]]) -> list[dict]:
    """DynamoDB Vector Search のレスポンスを eden.search.query_vectors 互換形式に変換する。

    DynamoDB search_vectors レスポンス形式:
        [{"Item": {...}, "Distance": float}, ...]

    eden.search.query_vectors 互換形式:
        [{"distance": float, "metadata": {"object_id": str, "text_tokens": list}}, ...]

    Args:
        ddb_hits: DynamoDB search_vectors の生レスポンス

    Returns:
        正規化されたヒットリスト
    """
    normalized = []
    for hit in ddb_hits:
        distance = hit.get("Distance", 0.0)

        # DynamoDB アイテムからメタデータを抽出
        item = hit.get("Item", {})

        # vector_id からobject_idを取得（フォールバック対応）
        vector_id = item.get("vector_id", {}).get("S", "")
        try:
            parsed = parse_vector_id(vector_id)
            object_id = parsed["object_id"]
        except ValueError:
            # vector_idパース失敗時はobject_idフィールドから取得
            object_id = item.get("object_id", {}).get("S", "unknown")

        # text_tokens の取得 (SS型 = String Set)
        text_tokens_raw = item.get("text_tokens", {}).get("SS", [])
        text_tokens = list(text_tokens_raw) if text_tokens_raw else []

        normalized.append({
            "distance": distance,
            "metadata": {
                "object_id": object_id,
                "text_tokens": text_tokens,
            },
        })

    return normalized


def check_vector_index_active(
    table_name: str = DDB_TABLE_NAME,
    index_name: str = DDB_INDEX_NAME,
    *,
    client=None,
    session: boto3.Session | None = None,
) -> bool:
    """DynamoDB テーブルのベクトルインデックスがアクティブか確認する。

    Args:
        table_name: DynamoDBテーブル名
        index_name: ベクトルインデックス名
        client: DynamoDB クライアント (Noneなら生成)
        session: boto3セッション

    Returns:
        インデックスがACTIVEならTrue
    """
    if client is None:
        s = session or boto3.Session()
        client = s.client("dynamodb", region_name=config.VECTOR_REGION)

    try:
        resp = client.describe_table(TableName=table_name)
        table_desc = resp.get("Table", {})
        vector_indexes = table_desc.get("VectorIndexes", [])

        for idx in vector_indexes:
            if idx.get("IndexName") == index_name:
                return idx.get("IndexStatus") == "ACTIVE"

        return False
    except client.exceptions.ResourceNotFoundException:
        return False


def query_vectors_ddb(
    vector: list[float],
    *,
    top_k: int | None = None,
    table_name: str = DDB_TABLE_NAME,
    index_name: str = DDB_INDEX_NAME,
    client=None,
    session: boto3.Session | None = None,
) -> list[dict]:
    """DynamoDB Vector Search にクエリを投げてヒットリストを返す。

    eden.search.query_vectors と同じインターフェースで結果を返すため、
    compute_scores / judge_verdict をそのまま適用可能。

    Args:
        vector: クエリベクトル (float32のリスト)
        top_k: 返却件数 (Noneなら config.TOP_K)
        table_name: DynamoDBテーブル名
        index_name: ベクトルインデックス名
        client: DynamoDB クライアント
        session: boto3セッション

    Returns:
        正規化されたヒットリスト (eden.search互換)
    """
    if client is None:
        s = session or boto3.Session()
        client = s.client("dynamodb", region_name=config.VECTOR_REGION)

    top_k = top_k or config.TOP_K

    resp = client.search_vectors(
        TableName=table_name,
        IndexName=index_name,
        SearchVector=vector,
        TopK=top_k,
    )

    ddb_hits = resp.get("Hits", [])
    return _normalize_ddb_response(ddb_hits)


def search_object_ddb(
    img: Image.Image,
    *,
    embed_client=None,
    ddb_client=None,
    text_client=None,
    table_name: str = DDB_TABLE_NAME,
    index_name: str = DDB_INDEX_NAME,
    session: boto3.Session | None = None,
) -> SearchResult:
    """DynamoDB Vector Search を使って画像検索を実行する。

    eden.search.search_object と同等の処理フローだが、
    ベクトル検索部分のみ DynamoDB Vector Search を使用する。

    Args:
        img: クロップ済み画像
        embed_client: bedrock-runtime クライアント (Embeddings用)
        ddb_client: DynamoDB クライアント
        text_client: bedrock-runtime クライアント (テキスト抽出用)
        table_name: DynamoDBテーブル名
        index_name: ベクトルインデックス名
        session: boto3セッション

    Returns:
        SearchResult
    """
    # 文字抽出
    raw_text = extract_text(img, client=text_client)
    query_tokens = normalize_text(raw_text)

    # 埋め込み
    vec = embed_image(img, client=embed_client)

    # DynamoDB Vector Search
    hits = query_vectors_ddb(
        vec.tolist(),
        client=ddb_client,
        table_name=table_name,
        index_name=index_name,
        session=session,
    )

    if not hits:
        return SearchResult(
            verdict="red",
            reason="no_hits",
            query_tokens=query_tokens,
        )

    # スコア計算 + 文字ブースト (既存ロジック再利用)
    hits, text_matched_ids = compute_scores(hits, query_tokens)

    # 3値判定 (既存ロジック再利用)
    result = judge_verdict(hits, text_matched_ids)

    return SearchResult(
        verdict=result["verdict"],
        object_id=result["object_id"],
        sim=result["sim"],
        sim_raw=result["sim_raw"],
        raw_top1_id=result["raw_top1_id"],
        rank_changed=result["rank_changed"],
        margin=result["margin"],
        votes=result["votes"],
        text_hit=result["text_hit"],
        query_tokens=query_tokens,
    )
