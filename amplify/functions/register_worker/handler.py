"""SQS trigger: 登録処理の非同期ワーカー。

ガードレール → クロップ → S3保存 → 文字抽出 → 4方向埋め込み → S3 Vectors/DynamoDB登録。
"""

import json
import logging
import os
import time
import uuid
from io import BytesIO

import boto3
from PIL import Image

from eden import config
from eden.crop import crop_bbox
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


def handler(event, context):
    for record in event["Records"]:
        msg = json.loads(record["body"])
        job_id = msg["job_id"]
        logger.info("[register-worker] ======= ジョブ開始: job_id=%s =======", job_id)
        logger.info("[register-worker] メッセージ: key=%s, box=%s, object_id=%s",
                    msg.get("key"), msg.get("box"), msg.get("object_id"))

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
    key = msg["key"]
    box = msg["box"]
    object_id = msg.get("object_id") or str(uuid.uuid4())
    logger.info("[register-worker] object_id=%s (新規=%s)", object_id, msg.get("object_id") is None)

    # S3から画像取得
    t0 = time.time()
    obj = s3_client.get_object(Bucket=IMAGE_BUCKET, Key=key)
    img = Image.open(BytesIO(obj["Body"].read()))
    img = exif_transpose(img).convert("RGB")
    img.thumbnail((config.MAX_IMAGE_SIZE, config.MAX_IMAGE_SIZE))
    logger.info("[register-worker] 画像取得: size=%s, %.1fms", img.size, (time.time() - t0) * 1000)

    # クロップ
    t1 = time.time()
    crop_img = crop_bbox(img, box)
    logger.info("[register-worker] クロップ完了: box=%s, crop_size=%s, %.1fms",
                box, crop_img.size, (time.time() - t1) * 1000)

    # ガードレール
    t2 = time.time()
    result = check_guardrail(jpeg_bytes(crop_img), client=rekognition_client)
    logger.info("[register-worker] ガードレール: passed=%s, reason=%s, %.1fms",
                result.passed, result.reason, (time.time() - t2) * 1000)
    if not result.passed:
        raise ValueError(
            f"Guardrail rejected: {result.reason} - {result.details}"
        )

    # クロップ画像をS3に保存 (再埋め込み原本 + サムネイル)
    crop_key = f"crops/{object_id}/{key.rsplit('/', 1)[-1]}"
    s3_client.put_object(
        Bucket=IMAGE_BUCKET,
        Key=crop_key,
        Body=jpeg_bytes(crop_img),
        ContentType="image/jpeg",
    )
    logger.info("[register-worker] クロップ画像S3保存: %s", crop_key)

    # サムネイル
    thumb = crop_img.copy()
    thumb.thumbnail((256, 256))
    thumb_key = f"thumbnails/{object_id}.jpg"
    s3_client.put_object(
        Bucket=IMAGE_BUCKET,
        Key=thumb_key,
        Body=jpeg_bytes(thumb, quality=75),
        ContentType="image/jpeg",
    )
    logger.info("[register-worker] サムネイル保存: %s", thumb_key)

    # 文字抽出
    t3 = time.time()
    raw_text = extract_text(crop_img, client=text_client)
    tokens = normalize_text(raw_text)
    logger.info("[register-worker] OCR完了: raw_text='%s', tokens=%s, %.1fms",
                raw_text[:50] if raw_text else "", tokens, (time.time() - t3) * 1000)

    # 4方向埋め込み → S3 Vectors 登録
    t4 = time.time()
    rotations = embed_rotations(crop_img, client=embed_client)
    logger.info("[register-worker] 埋め込み完了: %d方向, %.1fms", len(rotations), (time.time() - t4) * 1000)

    vectors = []
    for deg, vec in rotations:
        metadata = {
            "object_id": object_id,
            "rotation": deg,
            "label": object_id,
            "source_file": key,
        }
        if tokens:
            metadata["text_tokens"] = tokens

        vectors.append({
            "key": f"{object_id}__{key.rsplit('/', 1)[-1].rsplit('.', 1)[0]}__{deg}",
            "data": {"float32": vec.tolist()},
            "metadata": metadata,
        })

    # S3 Vectors に登録
    t5 = time.time()
    s3v_client.put_vectors(
        vectorBucketName=config.VECTOR_BUCKET,
        indexName=config.VECTOR_INDEX,
        vectors=vectors,
    )
    logger.info("[register-worker] S3 Vectors 登録完了: %d ベクトル, bucket=%s, index=%s, %.1fms",
                len(vectors), config.VECTOR_BUCKET, config.VECTOR_INDEX, (time.time() - t5) * 1000)

    # DynamoDB 物体テーブルに登録 (初回のみ作成、追加登録時はサムネ更新)
    try:
        objects_table.put_item(
            Item={
                "object_id": object_id,
                "thumbnail_key": thumb_key,
                "info": [],
            },
            ConditionExpression="attribute_not_exists(object_id)",
        )
        logger.info("[register-worker] DynamoDB 物体レコード新規作成: object_id=%s", object_id)
    except dynamodb.meta.client.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # 既存レコード → サムネだけ更新
            objects_table.update_item(
                Key={"object_id": object_id},
                UpdateExpression="SET thumbnail_key = :tk",
                ExpressionAttributeValues={":tk": thumb_key},
            )
            logger.info("[register-worker] DynamoDB 既存レコードのサムネ更新: object_id=%s", object_id)
        else:
            raise

    # ジョブテーブルに object_id を記録
    jobs_table.update_item(
        Key={"job_id": msg["job_id"]},
        UpdateExpression="SET object_id = :oid",
        ExpressionAttributeValues={":oid": object_id},
    )
    logger.info("[register-worker] ジョブに object_id 紐づけ完了: job_id=%s, object_id=%s", msg["job_id"], object_id)


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
