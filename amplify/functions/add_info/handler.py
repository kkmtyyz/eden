"""POST /objects/{id}/info: DynamoDB の info リストに追記する。"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

OBJECTS_TABLE = os.environ["OBJECTS_TABLE"]

dynamodb = boto3.resource("dynamodb")
objects_table = dynamodb.Table(OBJECTS_TABLE)


def handler(event, context):
    object_id = event["pathParameters"]["id"]
    body = json.loads(event.get("body") or "{}")
    text = body.get("text", "").strip()

    logger.info("[add-info] リクエスト受信: object_id=%s, text='%s'", object_id, text[:50])

    if not text:
        logger.warning("[add-info] text が空")
        return _error(400, "text is required")

    # Cognito authorizer から user_id を取得
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    user_id = claims.get("sub", "unknown")
    logger.info("[add-info] user_id=%s", user_id)

    now = datetime.now(timezone.utc).isoformat()

    # info リストに追記 (物体レコードがなければ新規作成)
    try:
        objects_table.update_item(
            Key={"object_id": object_id},
            UpdateExpression="SET info = list_append(if_not_exists(info, :empty), :new_info)",
            ExpressionAttributeValues={
                ":new_info": [{"text": text, "user_id": user_id, "created_at": now}],
                ":empty": [],
            },
        )
        logger.info("[add-info] 情報追記成功: object_id=%s", object_id)
    except Exception as e:
        logger.error("[add-info] DynamoDB 更新失敗: object_id=%s, error=%s", object_id, str(e))
        return _error(500, f"failed to update: {str(e)}")

    return {
        "statusCode": 200,
        "headers": _headers(),
        "body": json.dumps({"message": "info added", "object_id": object_id}),
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
