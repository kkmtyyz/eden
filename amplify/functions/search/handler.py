"""POST /search: BBox検出 → 上位5個を並列処理 → 3値判定 → 結果返却。"""

import asyncio
import json
import logging
import os
import time
from io import BytesIO

import boto3
from PIL import Image

from eden import config
from eden.crop import crop_bbox, select_center_object
from eden.detection import detect_boxes
from eden.image_util import exif_transpose, jpeg_bytes
from eden.search import search_object

logger = logging.getLogger()
logger.setLevel(logging.INFO)

IMAGE_BUCKET = os.environ["IMAGE_BUCKET"]
OBJECTS_TABLE = os.environ["OBJECTS_TABLE"]

s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
objects_table = dynamodb.Table(OBJECTS_TABLE)
bedrock_client = boto3.client("bedrock-runtime", region_name=config.BBOX_REGION)
embed_client = boto3.client("bedrock-runtime", region_name=config.EMBED_REGION)
text_client = boto3.client("bedrock-runtime", region_name=config.TEXT_REGION)
s3v_client = boto3.client("s3vectors", region_name=config.VECTOR_REGION)

MAX_CANDIDATES = 10


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    key = body.get("key")
    boxes_override = body.get("boxes")  # フロントで選択済みのBBoxを渡す場合

    logger.info("[search] リクエスト受信: key=%s, boxes_override=%s", key, boxes_override is not None)

    if not key:
        logger.warning("[search] key が未指定")
        return _error(400, "key is required")

    # S3から画像取得
    t0 = time.time()
    obj = s3_client.get_object(Bucket=IMAGE_BUCKET, Key=key)
    img = Image.open(BytesIO(obj["Body"].read()))
    img = exif_transpose(img).convert("RGB")
    img.thumbnail((config.MAX_IMAGE_SIZE, config.MAX_IMAGE_SIZE))
    logger.info("[search] 画像取得: size=%s, %.1fms", img.size, (time.time() - t0) * 1000)

    # BBox検出 (オーバーライドがなければ自動検出)
    if boxes_override:
        boxes = boxes_override
        logger.info("[search] BBox オーバーライド使用: %d個", len(boxes))
    else:
        t1 = time.time()
        boxes = detect_boxes(img, client=bedrock_client)
        logger.info("[search] BBox検出完了: %d個, %.1fms", len(boxes), (time.time() - t1) * 1000)

    if not boxes:
        logger.info("[search] 物体検出なし → 空レスポンス")
        return {
            "statusCode": 200,
            "headers": _headers(),
            "body": json.dumps({"results": [], "message": "no objects detected"}),
        }

    # 中央スコア順に上位N個を選択
    scored = sorted(boxes, key=lambda b: _center_score(b))
    candidates = scored[:MAX_CANDIDATES]
    logger.info("[search] 候補選定: %d → %d個", len(boxes), len(candidates))

    # 各候補を検索 (asyncioで並列化)
    t2 = time.time()
    results = asyncio.run(_search_candidates(img, candidates))
    logger.info("[search] 全候補検索完了: %.1fms", (time.time() - t2) * 1000)

    # 結果サマリー
    for i, r in enumerate(results):
        logger.info("[search] 結果[%d]: verdict=%s, sim=%.3f, margin=%.3f, object_id=%s, text_hit=%s",
                    i, r["verdict"], r["sim"], r["margin"], r["object_id"], r["text_hit"])

    return {
        "statusCode": 200,
        "headers": _headers(),
        "body": json.dumps({"results": results}),
    }


async def _search_candidates(img: Image.Image, candidates: list) -> list:
    """各BBox候補を並列に検索する。"""
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, _search_single, img, box)
        for box in candidates
    ]
    return await asyncio.gather(*tasks)


def _search_single(img: Image.Image, box: list) -> dict:
    """1つのBBoxに対して検索を実行する。"""
    t0 = time.time()
    crop_img = crop_bbox(img, box)
    logger.info("[search] BBox検索開始: box=%s, crop_size=%s", box, crop_img.size)

    result = search_object(
        crop_img,
        embed_client=embed_client,
        s3v_client=s3v_client,
        text_client=text_client,
    )
    logger.info("[search] BBox検索完了: box=%s, verdict=%s, sim=%.3f, %.1fms",
                box, result.verdict, result.sim, (time.time() - t0) * 1000)

    # 物体情報を付加
    info = None
    if result.object_id and result.verdict in ("green", "yellow"):
        try:
            resp = objects_table.get_item(Key={"object_id": result.object_id})
            item = resp.get("Item", {})
            info = {
                "object_id": result.object_id,
                "thumbnail_key": item.get("thumbnail_key"),
                "info": item.get("info", []),
            }
            logger.info("[search] DynamoDB 物体情報取得: object_id=%s, info_count=%d",
                        result.object_id, len(item.get("info", [])))
        except Exception as e:
            logger.warning("[search] DynamoDB 物体情報取得失敗: object_id=%s, error=%s", result.object_id, str(e))

    return {
        "box": box,
        "verdict": result.verdict,
        "object_id": result.object_id,
        "sim": result.sim,
        "margin": result.margin,
        "votes": result.votes,
        "text_hit": result.text_hit,
        "object_info": info,
    }


def _center_score(box: list) -> float:
    """中央に近い + 面積が大きいほどスコアが小さい (sort用)。"""
    x1, y1, x2, y2 = box
    cx = (x1 + x2) / 2 / 1000
    cy = (y1 + y2) / 2 / 1000
    dist = ((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5
    area = (x2 - x1) * (y2 - y1) / 1_000_000
    return dist - area * 0.3


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
