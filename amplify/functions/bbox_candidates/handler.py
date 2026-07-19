"""POST /bbox-candidates: BBox検出 → 候補返却。"""

import json
import logging
import os
import time
from io import BytesIO

import boto3
from PIL import Image

from eden.crop import select_center_object
from eden.detection import detect_boxes
from eden.image_util import exif_transpose

logger = logging.getLogger()
logger.setLevel(logging.INFO)

IMAGE_BUCKET = os.environ["IMAGE_BUCKET"]

s3_client = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime", region_name="ap-northeast-1")


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    key = body.get("key")

    logger.info("[bbox-candidates] リクエスト受信: key=%s", key)

    if not key:
        logger.warning("[bbox-candidates] key が未指定")
        return _error(400, "key is required")

    # S3から画像を取得
    t0 = time.time()
    obj = s3_client.get_object(Bucket=IMAGE_BUCKET, Key=key)
    img = Image.open(BytesIO(obj["Body"].read()))
    img = exif_transpose(img).convert("RGB")
    original_size = img.size
    img.thumbnail((1024, 1024))
    logger.info("[bbox-candidates] 画像取得完了: original=%s, resized=%s, %.1fms",
                original_size, img.size, (time.time() - t0) * 1000)

    # BBox検出
    t1 = time.time()
    boxes = detect_boxes(img, client=bedrock_client)
    logger.info("[bbox-candidates] BBox検出完了: %d個, %.1fms", len(boxes), (time.time() - t1) * 1000)

    if not boxes:
        logger.info("[bbox-candidates] 物体検出なし → 空レスポンス")
        return {
            "statusCode": 200,
            "headers": _headers(),
            "body": json.dumps({"boxes": [], "default_index": None, "image_size": list(img.size)}),
        }

    # 中央×面積スコアでデフォルト候補を算出
    default_box = select_center_object(boxes)
    default_index = boxes.index(default_box)
    logger.info("[bbox-candidates] デフォルト候補: index=%d, box=%s", default_index, default_box)

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
