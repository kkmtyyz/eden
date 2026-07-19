"""SQS trigger: 登録処理の非同期ワーカー。

ガードレール → クロップ → S3保存 → 文字抽出 → 4方向埋め込み → S3 Vectors/DynamoDB登録。
"""

import json
import os
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

        try:
            _update_job(job_id, "processing")
            _process(msg)
            _update_job(job_id, "completed")
        except Exception as e:
            _update_job(job_id, "failed", error=str(e))
            raise


def _process(msg: dict):
    key = msg["key"]
    box = msg["box"]
    object_id = msg.get("object_id") or str(uuid.uuid4())

    # S3から画像取得
    obj = s3_client.get_object(Bucket=IMAGE_BUCKET, Key=key)
    img = Image.open(BytesIO(obj["Body"].read()))
    img = exif_transpose(img).convert("RGB")
    img.thumbnail((config.MAX_IMAGE_SIZE, config.MAX_IMAGE_SIZE))

    # クロップ
    crop_img = crop_bbox(img, box)

    # ガードレール
    result = check_guardrail(jpeg_bytes(crop_img), client=rekognition_client)
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

    # 文字抽出
    raw_text = extract_text(crop_img, client=text_client)
    tokens = normalize_text(raw_text)

    # 4方向埋め込み → S3 Vectors 登録
    rotations = embed_rotations(crop_img, client=embed_client)
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
    s3v_client.put_vectors(
        vectorBucketName=config.VECTOR_BUCKET,
        indexName=config.VECTOR_INDEX,
        vectors=vectors,
    )

    # DynamoDB 物体テーブルに登録 (初回のみ作成、追加登録時はサムネ更新)
    objects_table.put_item(
        Item={
            "object_id": object_id,
            "thumbnail_key": thumb_key,
            "info": [],
        },
        ConditionExpression="attribute_not_exists(object_id)",
    )
    # 既に存在する場合はサムネだけ更新
    try:
        objects_table.update_item(
            Key={"object_id": object_id},
            UpdateExpression="SET thumbnail_key = :tk",
            ExpressionAttributeValues={":tk": thumb_key},
        )
    except Exception:
        pass  # put_item が成功していればここには来ない

    # ジョブテーブルに object_id を記録
    jobs_table.update_item(
        Key={"job_id": msg["job_id"]},
        UpdateExpression="SET object_id = :oid",
        ExpressionAttributeValues={":oid": object_id},
    )


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
