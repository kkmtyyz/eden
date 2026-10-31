"""運用ツール: DynamoDB VectorsTable と Objects/Jobs テーブルを空にする。

S3 Vectors のデータはそのまま残す。DynamoDB 側のみをリセットする版。
手動実行用 Lambda。API Gateway には接続せず、コンソールやCLIから直接 invoke する。

使い方:
    aws lambda invoke --function-name <FN> --payload '{"confirm": true}' out.json
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
VECTORS_TABLE = os.environ.get("EDEN_VECTORS_TABLE", config.VECTORS_TABLE)

dynamodb = boto3.resource("dynamodb")
objects_table = dynamodb.Table(OBJECTS_TABLE)
jobs_table = dynamodb.Table(JOBS_TABLE)
ddb_client = boto3.client("dynamodb", region_name=config.VECTOR_REGION)


def handler(event, context):
    """DynamoDB VectorsTable と Objects/Jobs テーブルを空にする。

    S3 Vectors は残す。event に {"confirm": true} を渡さないと実行しない（安全装置）。
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

    # ─── DynamoDB VectorsTable をクリア ─────────────────────────
    logger.info("[reset-ddb] DynamoDB VectorsTable クリア開始: table=%s", VECTORS_TABLE)
    try:
        results["vectors_deleted"] = _clear_vectors_table(VECTORS_TABLE)
    except Exception as e:
        logger.error("[reset-ddb] VectorsTable クリア失敗: %s", str(e))
        results["vectors_error"] = str(e)

    # ─── DynamoDB Objects テーブルをクリア ──────────────────────
    logger.info("[reset-ddb] DynamoDB Objects テーブルクリア開始: %s", OBJECTS_TABLE)
    try:
        results["objects_deleted"] = _clear_table(objects_table, "object_id")
    except Exception as e:
        logger.error("[reset-ddb] Objects テーブルクリア失敗: %s", str(e))
        results["objects_error"] = str(e)

    # ─── DynamoDB Jobs テーブルをクリア ─────────────────────────
    logger.info("[reset-ddb] DynamoDB Jobs テーブルクリア開始: %s", JOBS_TABLE)
    try:
        results["jobs_deleted"] = _clear_table(jobs_table, "job_id")
    except Exception as e:
        logger.error("[reset-ddb] Jobs テーブルクリア失敗: %s", str(e))
        results["jobs_error"] = str(e)

    logger.info("[reset-ddb] 完了: %s", results)
    return {
        "statusCode": 200,
        "body": json.dumps(results),
    }


def _clear_vectors_table(table_name: str) -> int:
    """DynamoDB VectorsTable の全アイテムを削除する。"""
    deleted = 0
    scan_kwargs = {
        "TableName": table_name,
        "ProjectionExpression": "vector_id",
    }

    while True:
        resp = ddb_client.scan(**scan_kwargs)
        items = resp.get("Items", [])
        if not items:
            break

        # 25件ずつ BatchWriteItem で削除
        for i in range(0, len(items), 25):
            batch = items[i:i + 25]
            request_items = {
                table_name: [
                    {"DeleteRequest": {"Key": {"vector_id": item["vector_id"]}}}
                    for item in batch
                ]
            }
            del_resp = ddb_client.batch_write_item(RequestItems=request_items)
            # UnprocessedItems リトライ
            unprocessed = del_resp.get("UnprocessedItems", {})
            while unprocessed:
                del_resp = ddb_client.batch_write_item(RequestItems=unprocessed)
                unprocessed = del_resp.get("UnprocessedItems", {})
            deleted += len(batch)

        logger.info("[reset-ddb] VectorsTable 削除: %d 件 (累計 %d)", len(items), deleted)

        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

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

        logger.info("[reset-ddb] DynamoDB 削除: %d 件 (累計 %d)", len(items), deleted)

        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    return deleted
