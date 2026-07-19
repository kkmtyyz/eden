"""POST /upload-url: S3 presigned PUT URL を発行する。"""

import json
import os
import uuid

from botocore.config import Config
import boto3

IMAGE_BUCKET = os.environ["IMAGE_BUCKET"]

# Signature V4 を明示的に指定（V2 だと CORS preflight で問題が出る場合あり）
s3_client = boto3.client(
    "s3",
    config=Config(signature_version="s3v4"),
)

# presigned URL の有効期限 (秒)
PRESIGNED_EXPIRY = 300  # 5分


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    content_type = body.get("content_type", "image/jpeg")
    filename = body.get("filename", "image.jpg")

    # ユニークなキーを生成
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
    key = f"uploads/{uuid.uuid4()}.{ext}"

    # presigned PUT URL 発行 (Signature V4)
    url = s3_client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": IMAGE_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=PRESIGNED_EXPIRY,
    )

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({
            "upload_url": url,
            "key": key,
            "expires_in": PRESIGNED_EXPIRY,
        }),
    }
