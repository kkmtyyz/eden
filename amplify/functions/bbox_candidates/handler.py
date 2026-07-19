"""POST /bbox-candidates: BBox検出 → 候補返却。"""

import json
import os
from io import BytesIO

import boto3
from PIL import Image

from eden.crop import select_center_object
from eden.detection import detect_boxes
from eden.image_util import exif_transpose

IMAGE_BUCKET = os.environ["IMAGE_BUCKET"]

s3_client = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime", region_name="ap-northeast-1")


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    key = body.get("key")

    if not key:
        return _error(400, "key is required")

    # S3から画像を取得
    obj = s3_client.get_object(Bucket=IMAGE_BUCKET, Key=key)
    img = Image.open(BytesIO(obj["Body"].read()))
    img = exif_transpose(img).convert("RGB")
    img.thumbnail((1024, 1024))

    # BBox検出
    boxes = detect_boxes(img, client=bedrock_client)

    if not boxes:
        return {
            "statusCode": 200,
            "headers": _headers(),
            "body": json.dumps({"boxes": [], "default_index": None}),
        }

    # 中央×面積スコアでデフォルト候補を算出
    default_box = select_center_object(boxes)
    default_index = boxes.index(default_box)

    return {
        "statusCode": 200,
        "headers": _headers(),
        "body": json.dumps({
            "boxes": boxes,
            "default_index": default_index,
            "image_size": list(img.size),
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
