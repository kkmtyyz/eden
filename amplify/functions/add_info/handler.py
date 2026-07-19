"""POST /objects/{id}/info: DynamoDB の info リストに追記する。"""

import json
import os
from datetime import datetime, timezone

import boto3

OBJECTS_TABLE = os.environ["OBJECTS_TABLE"]

dynamodb = boto3.resource("dynamodb")
objects_table = dynamodb.Table(OBJECTS_TABLE)


def handler(event, context):
    object_id = event["pathParameters"]["id"]
    body = json.loads(event.get("body") or "{}")
    text = body.get("text", "").strip()

    if not text:
        return _error(400, "text is required")

    # Cognito authorizer から user_id を取得
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    user_id = claims.get("sub", "unknown")

    now = datetime.now(timezone.utc).isoformat()

    # info リストに追記
    try:
        objects_table.update_item(
            Key={"object_id": object_id},
            UpdateExpression="SET info = list_append(if_not_exists(info, :empty), :new_info)",
            ExpressionAttributeValues={
                ":new_info": [{"text": text, "user_id": user_id, "created_at": now}],
                ":empty": [],
            },
            ConditionExpression="attribute_exists(object_id)",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return _error(404, "object not found")

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
