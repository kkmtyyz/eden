"""GET /jobs/{id}: ジョブ状態を返却する。"""

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

JOBS_TABLE = os.environ["JOBS_TABLE"]

dynamodb = boto3.resource("dynamodb")
jobs_table = dynamodb.Table(JOBS_TABLE)


def handler(event, context):
    job_id = event["pathParameters"]["id"]
    logger.info("[job-status] リクエスト受信: job_id=%s", job_id)

    resp = jobs_table.get_item(Key={"job_id": job_id})
    item = resp.get("Item")

    if not item:
        logger.warning("[job-status] ジョブ未発見: job_id=%s", job_id)
        return _error(404, "job not found")

    status = item["status"]
    logger.info("[job-status] ジョブ状態: job_id=%s, status=%s, object_id=%s",
                job_id, status, item.get("object_id") or "N/A")

    return {
        "statusCode": 200,
        "headers": _headers(),
        "body": json.dumps({
            "job_id": item["job_id"],
            "status": status,
            "object_id": item.get("object_id") or None,
            "created_at": item.get("created_at"),
            "error": item.get("error"),
        }),
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
