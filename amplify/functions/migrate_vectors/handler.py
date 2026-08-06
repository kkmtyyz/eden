"""運用ツール: S3 Vectors から DynamoDB VectorsTable へデータを移行する。

手動実行用 Lambda。API Gateway には接続せず、コンソールやCLIから直接 invoke する。

使い方:
    aws lambda invoke --function-name <FN> --payload '{"confirm": true}' out.json

オプション:
    confirm (bool): true を渡さないと実行しない（安全装置）
    batch_size (int): DynamoDB バッチ書き込みサイズ (デフォルト: 25, 最大: 25)
"""

import json
import logging
import os
import time

import boto3

from eden import config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 環境変数
VECTORS_TABLE = os.environ.get("EDEN_VECTORS_TABLE", config.VECTORS_TABLE)
VECTOR_BUCKET = os.environ.get("EDEN_VECTOR_BUCKET", "")
VECTOR_INDEX = os.environ.get("EDEN_VECTOR_INDEX", "eden-index")

# クライアント
s3v_client = boto3.client("s3vectors", region_name=config.VECTOR_REGION)
ddb_client = boto3.client("dynamodb", region_name=config.VECTOR_REGION)

# vector_id セパレータ
_SEPARATOR = "##"


def handler(event, context):
    """S3 Vectors の全ベクトルを DynamoDB VectorsTable に移行する。

    event に {"confirm": true} を渡さないと実行しない（安全装置）。
    """
    if not event.get("confirm"):
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "Safety check: pass {\"confirm\": true} to execute migration.",
            }),
        }

    batch_size = min(event.get("batch_size", 25), 25)

    if not VECTOR_BUCKET:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "EDEN_VECTOR_BUCKET environment variable is not set.",
            }),
        }

    logger.info("[migrate] === S3 Vectors → DynamoDB 移行開始 ===")
    logger.info("[migrate] 移行元: bucket=%s, index=%s", VECTOR_BUCKET, VECTOR_INDEX)
    logger.info("[migrate] 移行先: table=%s", VECTORS_TABLE)

    try:
        # Phase 1: S3 Vectors から全ベクトルを取得
        vectors = _fetch_all_s3_vectors()
        logger.info("[migrate] S3 Vectors 取得完了: %d 件", len(vectors))

        if not vectors:
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "No vectors to migrate.",
                    "vectors_fetched": 0,
                    "vectors_written": 0,
                }),
            }

        # Phase 2: DynamoDB にバッチ書き込み
        written = _write_to_dynamodb(vectors, batch_size)
        logger.info("[migrate] DynamoDB 書き込み完了: %d 件", written)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Migration completed successfully.",
                "vectors_fetched": len(vectors),
                "vectors_written": written,
            }),
        }

    except Exception as e:
        logger.error("[migrate] 移行失敗: %s", str(e), exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": f"Migration failed: {str(e)}",
            }),
        }


def _fetch_all_s3_vectors() -> list[dict]:
    """S3 Vectors から全ベクトルデータ (メタデータ + ベクトル値) を取得する。"""
    all_vectors = []
    next_token = None

    while True:
        kwargs: dict = {
            "vectorBucketName": VECTOR_BUCKET,
            "indexName": VECTOR_INDEX,
            "returnData": True,
            "returnMetadata": True,
        }
        if next_token:
            kwargs["nextToken"] = next_token

        resp = s3v_client.list_vectors(**kwargs)
        vectors = resp.get("vectors", [])
        all_vectors.extend(vectors)

        next_token = resp.get("nextToken")
        if not next_token:
            break

        logger.info("[migrate] S3 Vectors 取得中... %d 件", len(all_vectors))

    return all_vectors


def _write_to_dynamodb(vectors: list[dict], batch_size: int) -> int:
    """取得したベクトルデータを DynamoDB VectorsTable にバッチ書き込みする。"""
    written = 0

    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i + batch_size]
        put_requests = []

        for vec in batch:
            item = _build_item(vec)
            if item:
                put_requests.append({"PutRequest": {"Item": item}})

        if not put_requests:
            continue

        # バッチ書き込み (UnprocessedItems リトライ)
        request_items = {VECTORS_TABLE: put_requests}
        while request_items:
            resp = ddb_client.batch_write_item(RequestItems=request_items)
            request_items = resp.get("UnprocessedItems", {})
            if request_items:
                time.sleep(0.5)  # スロットリング対策

        written += len(put_requests)

        if written % 100 == 0 or i + batch_size >= len(vectors):
            logger.info("[migrate] DynamoDB 書き込み: %d/%d 件", written, len(vectors))

    return written


def _build_item(vec: dict) -> dict | None:
    """S3 Vectors のベクトルエントリを DynamoDB アイテム形式に変換する。

    Args:
        vec: S3 Vectors の list_vectors レスポンスの1要素
            - key: ベクトルキー
            - data: {"float32": [float, ...]}
            - metadata: {object_id, source_file, rotation, text_tokens?, ...}

    Returns:
        DynamoDB 形式のアイテム辞書。変換不能な場合は None。
    """
    metadata = vec.get("metadata", {})
    object_id = metadata.get("object_id")
    source_file = metadata.get("source_file", "")
    rotation = metadata.get("rotation", 0)

    # ベクトルデータの取得
    vector_data = vec.get("data", {}).get("float32", [])
    if not vector_data or not object_id:
        logger.warning("[migrate] ベクトルデータまたは object_id が欠落: key=%s", vec.get("key"))
        return None

    # vector_id 構築: {object_id}##{source_file}##{rotation}
    vector_id = f"{object_id}{_SEPARATOR}{source_file}{_SEPARATOR}{rotation}"

    item: dict = {
        "vector_id": {"S": vector_id},
        "object_id": {"S": object_id},
        "source_file": {"S": source_file},
        "rotation": {"N": str(rotation)},
        "vector": {"L": [{"N": str(v)} for v in vector_data]},
    }

    # text_tokens は非空の場合のみ
    text_tokens = metadata.get("text_tokens")
    if text_tokens:
        item["text_tokens"] = {"SS": text_tokens}

    return item
