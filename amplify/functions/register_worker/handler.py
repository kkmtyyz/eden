"""SQS trigger: 登録処理の非同期ワーカー。

マルチフレーム対応:
代表フレームでガードレール＆BBox確定 → 全フレームでクロップ→文字抽出→4方向埋め込み→登録。
"""

import json
import logging
import os
import time
import uuid
from io import BytesIO

import boto3
from botocore.exceptions import ClientError
from PIL import Image

from eden import config
from eden.crop import crop_bbox
from eden.detection import detect_boxes
from eden.embedding import embed_rotations
from eden.guardrail import check_guardrail
from eden.image_util import exif_transpose, jpeg_bytes
from eden.ocr import extract_text, normalize_text

logger = logging.getLogger()
logger.setLevel(logging.INFO)

IMAGE_BUCKET = os.environ["IMAGE_BUCKET"]
OBJECTS_TABLE = os.environ["OBJECTS_TABLE"]
JOBS_TABLE = os.environ["JOBS_TABLE"]

s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
objects_table = dynamodb.Table(OBJECTS_TABLE)
jobs_table = dynamodb.Table(JOBS_TABLE)
bedrock_client = boto3.client("bedrock-runtime", region_name=config.BBOX_REGION)
embed_client = boto3.client("bedrock-runtime", region_name=config.EMBED_REGION)
text_client = boto3.client("bedrock-runtime", region_name=config.TEXT_REGION)
rekognition_client = boto3.client("rekognition", region_name=config.REKOGNITION_REGION)
s3v_client = boto3.client("s3vectors", region_name=config.VECTOR_REGION)

# BBox追跡時のIoU閾値
IOU_THRESHOLD = 0.3


def handler(event, context):
    for record in event["Records"]:
        msg = json.loads(record["body"])
        job_id = msg["job_id"]
        logger.info("[register-worker] ======= ジョブ開始: job_id=%s =======", job_id)

        keys = msg.get("keys", [])
        # 後方互換
        if not keys and msg.get("key"):
            keys = [msg["key"]]
            msg["keys"] = keys
            msg["representative_key"] = msg.get("representative_key") or msg["key"]

        logger.info("[register-worker] フレーム数: %d, representative_key=%s, box=%s, object_id=%s",
                    len(keys), msg.get("representative_key"), msg.get("box"), msg.get("object_id"))

        try:
            _update_job(job_id, "processing")
            t0 = time.time()
            _process(msg)
            elapsed = time.time() - t0
            _update_job(job_id, "completed")
            logger.info("[register-worker] ======= ジョブ完了: job_id=%s, %.1f秒 =======", job_id, elapsed)
        except Exception as e:
            logger.error("[register-worker] ジョブ失敗: job_id=%s, error=%s", job_id, str(e), exc_info=True)
            _update_job(job_id, "failed", error=str(e))
            raise


def _process(msg: dict):
    keys = msg["keys"]
    representative_key = msg["representative_key"]
    box = msg["box"]
    object_id = msg.get("object_id") or str(uuid.uuid4())
    initial_info = msg.get("initial_info", "").strip()
    logger.info("[register-worker] object_id=%s (新規=%s), initial_info='%s'",
                object_id, msg.get("object_id") is None, initial_info[:30] if initial_info else "")

    # ─── 代表フレーム処理 ──────────────────────────────────────
    t0 = time.time()
    rep_img = _load_image(representative_key)
    logger.info("[register-worker] 代表画像取得: key=%s, size=%s, %.1fms",
                representative_key, rep_img.size, (time.time() - t0) * 1000)

    # ガードレール（代表フレームのクロップで実施）
    rep_crop = crop_bbox(rep_img, box)
    t1 = time.time()
    guard_result = check_guardrail(jpeg_bytes(rep_crop), client=rekognition_client)
    logger.info("[register-worker] ガードレール: passed=%s, reason=%s, %.1fms",
                guard_result.passed, guard_result.reason, (time.time() - t1) * 1000)
    if not guard_result.passed:
        raise ValueError(f"Guardrail rejected: {guard_result.reason} - {guard_result.details}")

    # ─── 全フレーム処理 ────────────────────────────────────────
    all_vectors = []
    thumbnail_key = None

    for i, key in enumerate(keys):
        logger.info("[register-worker] フレーム %d/%d 処理開始: %s", i + 1, len(keys), key)
        t_frame = time.time()

        # 画像取得
        if key == representative_key:
            img = rep_img
            frame_box = box
        else:
            img = _load_image(key)
            # BBox追跡: 代表フレームで選択したBBoxと同じ物体を探す
            frame_box = _track_bbox(img, box)
            if frame_box is None:
                logger.info("[register-worker] フレーム %d: 物体追跡失敗 → スキップ", i + 1)
                continue

        # クロップ
        crop_img = crop_bbox(img, frame_box)
        logger.info("[register-worker] フレーム %d: クロップ完了, box=%s, crop_size=%s",
                    i + 1, frame_box, crop_img.size)

        # S3保存（再埋め込み原本）
        crop_key = f"crops/{object_id}/{key.rsplit('/', 1)[-1]}"
        s3_client.put_object(
            Bucket=IMAGE_BUCKET,
            Key=crop_key,
            Body=jpeg_bytes(crop_img),
            ContentType="image/jpeg",
        )

        # サムネイル（代表画像のみ）
        if key == representative_key:
            thumb = crop_img.copy()
            thumb.thumbnail((256, 256))
            thumbnail_key = f"thumbnails/{object_id}.jpg"
            s3_client.put_object(
                Bucket=IMAGE_BUCKET,
                Key=thumbnail_key,
                Body=jpeg_bytes(thumb, quality=75),
                ContentType="image/jpeg",
            )
            logger.info("[register-worker] サムネイル保存: %s", thumbnail_key)

        # 文字抽出
        t_ocr = time.time()
        raw_text = extract_text(crop_img, client=text_client)
        tokens = normalize_text(raw_text)
        logger.info("[register-worker] フレーム %d: OCR='%s', tokens=%s, %.1fms",
                    i + 1, raw_text[:30] if raw_text else "", tokens, (time.time() - t_ocr) * 1000)

        # 4方向埋め込み
        t_embed = time.time()
        rotations = embed_rotations(crop_img, client=embed_client)
        logger.info("[register-worker] フレーム %d: 埋め込み完了, %d方向, %.1fms",
                    i + 1, len(rotations), (time.time() - t_embed) * 1000)

        # ベクトル構築
        for deg, vec in rotations:
            metadata = {
                "object_id": object_id,
                "rotation": deg,
                "label": object_id,
                "source_file": key,
            }
            if tokens:
                metadata["text_tokens"] = tokens

            vec_key = f"{object_id}__{key.rsplit('/', 1)[-1].rsplit('.', 1)[0]}__{deg}"
            all_vectors.append({
                "key": vec_key,
                "data": {"float32": vec.tolist()},
                "metadata": metadata,
            })

        logger.info("[register-worker] フレーム %d/%d 完了: %.1fms",
                    i + 1, len(keys), (time.time() - t_frame) * 1000)

    # ─── S3 Vectors に一括登録 ─────────────────────────────────
    if not all_vectors:
        raise ValueError("有効なフレームがありませんでした")

    t_vec = time.time()
    # S3 Vectors は 1回 の put_vectors で最大100ベクトル
    BATCH_SIZE = 100
    for batch_start in range(0, len(all_vectors), BATCH_SIZE):
        batch = all_vectors[batch_start:batch_start + BATCH_SIZE]
        s3v_client.put_vectors(
            vectorBucketName=config.VECTOR_BUCKET,
            indexName=config.VECTOR_INDEX,
            vectors=batch,
        )
        logger.info("[register-worker] S3 Vectors バッチ登録: %d-%d / %d",
                    batch_start + 1, batch_start + len(batch), len(all_vectors))

    logger.info("[register-worker] S3 Vectors 全登録完了: %d ベクトル, %.1fms",
                len(all_vectors), (time.time() - t_vec) * 1000)

    # ─── DynamoDB 物体テーブル登録 ─────────────────────────────
    # 初期情報があれば info リストに入れる
    initial_info_list = []
    if initial_info:
        # Cognito user_id は SQS 経由では取れないので "system" とする
        # (register Lambda で event から取得して渡すことも可能だが、簡易実装)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        initial_info_list = [{"text": initial_info, "user_id": "registrant", "created_at": now}]

    try:
        objects_table.put_item(
            Item={
                "object_id": object_id,
                "thumbnail_key": thumbnail_key,
                "info": initial_info_list,
            },
            ConditionExpression="attribute_not_exists(object_id)",
        )
        logger.info("[register-worker] DynamoDB 物体レコード新規作成: object_id=%s, info_count=%d",
                    object_id, len(initial_info_list))
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # 既存レコード → サムネ更新 + 情報追記
            update_expr = "SET thumbnail_key = :tk"
            attr_values: dict = {":tk": thumbnail_key}
            if initial_info_list:
                update_expr += ", info = list_append(if_not_exists(info, :empty), :new_info)"
                attr_values[":new_info"] = initial_info_list
                attr_values[":empty"] = []
            objects_table.update_item(
                Key={"object_id": object_id},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=attr_values,
            )
            logger.info("[register-worker] DynamoDB 既存レコード更新: object_id=%s", object_id)
        else:
            raise

    # ジョブに object_id 紐づけ
    jobs_table.update_item(
        Key={"job_id": msg["job_id"]},
        UpdateExpression="SET object_id = :oid",
        ExpressionAttributeValues={":oid": object_id},
    )
    logger.info("[register-worker] ジョブに object_id 紐づけ完了: job_id=%s, object_id=%s",
                msg["job_id"], object_id)


def _load_image(key: str) -> Image.Image:
    """S3から画像を取得してリサイズ済みで返す。"""
    obj = s3_client.get_object(Bucket=IMAGE_BUCKET, Key=key)
    img = Image.open(BytesIO(obj["Body"].read()))
    img = exif_transpose(img).convert("RGB")
    img.thumbnail((config.MAX_IMAGE_SIZE, config.MAX_IMAGE_SIZE))
    return img


def _track_bbox(img: Image.Image, reference_box: list[int]) -> list[int] | None:
    """代表フレームのBBoxを基に、別フレームで同一物体のBBoxを追跡する。

    BBox検出 → reference_box との IoU が最大かつ閾値以上のものを返す。
    """
    t0 = time.time()
    boxes = detect_boxes(img, client=bedrock_client)
    logger.info("[register-worker] BBox追跡: 検出=%d個, %.1fms", len(boxes), (time.time() - t0) * 1000)

    if not boxes:
        return None

    best_box = None
    best_iou = 0.0

    for b in boxes:
        iou = _compute_iou(reference_box, b)
        if iou > best_iou:
            best_iou = iou
            best_box = b

    if best_iou < IOU_THRESHOLD:
        logger.info("[register-worker] BBox追跡: IoU=%.3f < 閾値%.2f → 追跡失敗", best_iou, IOU_THRESHOLD)
        return None

    logger.info("[register-worker] BBox追跡成功: IoU=%.3f, box=%s", best_iou, best_box)
    return best_box


def _compute_iou(box_a: list[int], box_b: list[int]) -> float:
    """2つのBBox (0-1000スケール) の IoU を計算。"""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0

    inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union_area = area_a + area_b - inter_area

    if union_area == 0:
        return 0.0
    return inter_area / union_area


def _update_job(job_id: str, status: str, error: str | None = None):
    update_expr = "SET #s = :s"
    attr_values: dict = {":s": status}
    attr_names = {"#s": "status"}

    if error:
        update_expr += ", #e = :e"
        attr_values[":e"] = error
        attr_names["#e"] = "error"

    jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=attr_values,
        ExpressionAttributeNames=attr_names,
    )
    logger.info("[register-worker] ジョブステータス更新: job_id=%s, status=%s", job_id, status)
