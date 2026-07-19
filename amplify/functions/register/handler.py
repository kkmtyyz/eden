"""POST /register: SQSへ登録ジョブを投入し、job_id を返す (202)。"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

JOBS_TABLE = os.environ["JOBS_TABLE"]
REGISTER_QUEUE_URL = os.environ["REGISTER_QUEUE_URL"]

dynamodb = boto3.resource("dynamodb")
jobs_table = dynamodb.Table(JOBS_TABLE)
sqs_client = boto3.client("sqs")


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    key = body.get("key")
    box = body.get("box")  # [x1, y1, x2, y2] (0-1000スケール)
    object_id = body.get("object_id")  # 新規登録時はNone、追加登録時は既存ID

    logger.info("[register] リクエスト受信: key=%s, box=%s, object_id=%s", key, box, object_id)

    if not key:
        logger.warning("[register] key が未指定")
        return _error(400, "key is required")
    if not box:
        logger.warning("[register] box が未指定")
        return _error(400, "box is required")

    # ジョブID生成
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    logger.info("[register] ジョブ作成: job_id=%s", job_id)

    # DynamoDB にジョブレコード作成
    jobs_table.put_item(
        Item={
            "job_id": job_id,
            "status": "pending",
            "object_id": object_id or "",
            "created_at": now,
        }
    )
    logger.info("[register] ジョブレコード作成完了 (status=pending)")

    # SQS にメッセージ投入
    sqs_client.send_message(
        QueueUrl=REGISTER_QUEUE_URL,
        MessageBody=json.dumps({
            "job_id": job_id,
            "key": key,
            "box": box,
            "object_id": object_id,
        }),
    )
    logger.info("[register] SQS メッセージ送信完了: queue=%s", REGISTER_QUEUE_URL)

    return {
        "statusCode": 202,
        "headers": _headers(),
        "body": json.dumps({"job_id": job_id}),
    }


def _headers():
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
    }


def _error(status, message):
    return {
        "statusCode": status,
        "headers": _headers(),
        "body": json.dumps({"error": message}),
    }
