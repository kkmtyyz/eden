"""運用ツール: S3 Vectors インデックスと DynamoDB テーブルを空にする。

手動実行用 Lambda。API Gateway には接続せず、コンソールやCLIから直接 invoke する。
"""

import json
import logging
import os
import time

import boto3

from eden import config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

OBJECTS_TABLE = os.environ["OBJECTS_TABLE"]
JOBS_TABLE = os.environ["JOBS_TABLE"]

dynamodb = boto3.resource("dynamodb")
objects_table = dynamodb.Table(OBJECTS_TABLE)
jobs_table = dynamodb.Table(JOBS_TABLE)
s3v_client = boto3.client("s3vectors", region_name=config.VECTOR_REGION)


def handler(event, context):
    """S3 Vectors インデックスと DynamoDB テーブルを空にする。

    event に {"confirm": true} を渡さないと実行しない（安全装置）。
    """
    if not event.get("confirm"):
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "Safety check: pass {\"confirm\": true} to execute reset.",
            }),
        }

    results = {
        "vectors_deleted": 0,
        "objects_deleted": 0,
        "jobs_deleted": 0,
    }

    # ─── S3 Vectors インデックスをクリア ───────────────────────
    logger.info("[reset] S3 Vectors クリア開始: bucket=%s, index=%s",
                config.VECTOR_BUCKET, config.VECTOR_INDEX)
    try:
        results["vectors_deleted"] = _clear_vectors()
    except Exception as e:
        logger.error("[reset] S3 Vectors クリア失敗: %s", str(e))
        results["vectors_error"] = str(e)

    # ─── DynamoDB Objects テーブルをクリア ──────────────────────
    logger.info("[reset] DynamoDB Objects テーブルクリア開始: %s", OBJECTS_TABLE)
    try:
        results["objects_deleted"] = _clear_table(objects_table, "object_id")
    except Exception as e:
        logger.error("[reset] Objects テーブルクリア失敗: %s", str(e))
        results["objects_error"] = str(e)

    # ─── DynamoDB Jobs テーブルをクリア ─────────────────────────
    logger.info("[reset] DynamoDB Jobs テーブルクリア開始: %s", JOBS_TABLE)
    try:
        results["jobs_deleted"] = _clear_table(jobs_table, "job_id")
    except Exception as e:
        logger.error("[reset] Jobs テーブルクリア失敗: %s", str(e))
        results["jobs_error"] = str(e)

    logger.info("[reset] 完了: %s", results)
    return {
        "statusCode": 200,
        "body": json.dumps(results),
    }


def _clear_vectors() -> int:
    """S3 Vectors インデックスの全ベクトルを削除する。"""
    deleted = 0
    # list_vectors → delete_vectors のループ
    while True:
        resp = s3v_client.list_vectors(
            vectorBucketName=config.VECTOR_BUCKET,
            indexName=config.VECTOR_INDEX,
            maxResults=100,
        )
        vectors = resp.get("vectors", [])
        if not vectors:
            break

        keys = [v["key"] for v in vectors]
        s3v_client.delete_vectors(
            vectorBucketName=config.VECTOR_BUCKET,
            indexName=config.VECTOR_INDEX,
            keys=keys,
        )
        deleted += len(keys)
        logger.info("[reset] S3 Vectors 削除: %d 件 (累計 %d)", len(keys), deleted)

        # API スロットリング回避
        time.sleep(0.1)

    return deleted


def _clear_table(table, pk_name: str) -> int:
    """DynamoDB テーブルの全レコードを削除する (scan → batch_delete)。"""
    deleted = 0
    scan_kwargs = {"ProjectionExpression": pk_name}

    while True:
        resp = table.scan(**scan_kwargs)
        items = resp.get("Items", [])
        if not items:
            break

        with table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={pk_name: item[pk_name]})
                deleted += 1

        logger.info("[reset] DynamoDB 削除: %d 件 (累計 %d)", len(items), deleted)

        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    return deleted
