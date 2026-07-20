"""POST /download-url: S3 presigned GET URL を発行する。"""

import json
import logging
import os

from botocore.config import Config
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

IMAGE_BUCKET = os.environ["IMAGE_BUCKET"]
REGION = os.environ.get("AWS_REGION", "ap-northeast-1")

s3_client = boto3.client(
    "s3",
    region_name=REGION,
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "virtual"},
    ),
)

# presigned URL の有効期限 (秒)
PRESIGNED_EXPIRY = 600  # 10分


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    key = body.get("key")

    if not key:
        logger.warning("[download-url] key が未指定")
        return {
            "statusCode": 400,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": "key is required"}),
        }

    logger.info("[download-url] リクエスト受信: key=%s", key)

    # presigned GET URL 発行
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": IMAGE_BUCKET,
            "Key": key,
        },
        ExpiresIn=PRESIGNED_EXPIRY,
    )

    logger.info(
        "[download-url] presigned URL 発行完了: key=%s, bucket=%s, expires=%ds",
        key, IMAGE_BUCKET, PRESIGNED_EXPIRY,
    )

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({
            "download_url": url,
            "expires_in": PRESIGNED_EXPIRY,
        }),
    }
