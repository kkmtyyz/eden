"""POST /register: SQSへ登録ジョブを投入し、job_id を返す (202)。"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3

from eden.text_guardrail import check_text_guardrail

logger = logging.getLogger()
logger.setLevel(logging.INFO)

JOBS_TABLE = os.environ["JOBS_TABLE"]
REGISTER_QUEUE_URL = os.environ["REGISTER_QUEUE_URL"]

dynamodb = boto3.resource("dynamodb")
jobs_table = dynamodb.Table(JOBS_TABLE)
sqs_client = boto3.client("sqs")


def handler(event, context):
    body = json.loads(event.get("body") or "{}")

    # マルチフレーム対応: keys[] + representative_key
    keys = body.get("keys")              # 全フレームの S3 キー配列
    representative_key = body.get("representative_key")  # サムネ用代表画像
    box = body.get("box")                # [x1, y1, x2, y2] (0-1000スケール)
    object_id = body.get("object_id")    # 新規登録時はNone、追加登録時は既存ID
    initial_info = body.get("initial_info", "").strip()  # 初期物体情報

    # 後方互換: 旧 API (key 単体) もサポート
    if not keys:
        single_key = body.get("key")
        if single_key:
            keys = [single_key]
            representative_key = representative_key or single_key

    logger.info("[register] リクエスト受信: keys=%d枚, representative_key=%s, box=%s, object_id=%s, initial_info='%s'",
                len(keys) if keys else 0, representative_key, box, object_id, initial_info[:30] if initial_info else "")

    if not keys or len(keys) == 0:
        logger.warning("[register] keys が未指定")
        return _error(400, "keys is required")
    if not representative_key:
        logger.warning("[register] representative_key が未指定")
        return _error(400, "representative_key is required")
    if not box:
        logger.warning("[register] box が未指定")
        return _error(400, "box is required")

    # テキストガードレール: initial_info をチェック
    if initial_info:
        guard = check_text_guardrail(initial_info)
        if not guard.passed:
            logger.warning("[register] テキストガードレール NG: reason=%s, policy=%s",
                          guard.reason, guard.policy)
            return _error(400, f"コメントが不適切と判定されました: {guard.reason}")

    # ジョブID生成
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    logger.info("[register] ジョブ作成: job_id=%s, frame_count=%d", job_id, len(keys))

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
            "keys": keys,
            "representative_key": representative_key,
            "box": box,
            "object_id": object_id,
            "initial_info": initial_info,
        }),
    )
    logger.info("[register] SQS メッセージ送信完了: queue=%s, keys=%d枚", REGISTER_QUEUE_URL, len(keys))

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
