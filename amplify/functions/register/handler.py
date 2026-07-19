"""POST /register: SQSへ登録ジョブを投入し、job_id を返す (202)。"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3

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

    if not key:
        return _error(400, "key is required")
    if not box:
        return _error(400, "box is required")

    # ジョブID生成
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # DynamoDB にジョブレコード作成
    jobs_table.put_item(
        Item={
            "job_id": job_id,
            "status": "pending",
            "object_id": object_id or "",
            "created_at": now,
        }
    )

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
