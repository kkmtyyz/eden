"""SQS trigger: 登録処理の非同期ワーカー。

マルチフレーム対応 (並列化版):
Phase 1: BBox追跡を4並列で実行 (50枚 → 追跡成功フレームを特定)
Phase 2: 追跡成功フレームからベスト20枚を選定 (センタリング + 多様性)
Phase 3: OCR + 4方向埋め込みを4並列で実行
Phase 4: S3 Vectors に一括登録 + DynamoDB 更新
"""

import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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
VECTORS_TABLE = os.environ.get("EDEN_VECTORS_TABLE", config.VECTORS_TABLE)

s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
objects_table = dynamodb.Table(OBJECTS_TABLE)
jobs_table = dynamodb.Table(JOBS_TABLE)
bedrock_client = boto3.client("bedrock-runtime", region_name=config.BBOX_REGION)
embed_client = boto3.client("bedrock-runtime", region_name=config.EMBED_REGION)
text_client = boto3.client("bedrock-runtime", region_name=config.TEXT_REGION)
rekognition_client = boto3.client("rekognition", region_name=config.REKOGNITION_REGION)
ddb_vectors_client = boto3.client("dynamodb", region_name=config.VECTOR_REGION)

# BBox追跡時のIoU閾値
IOU_THRESHOLD = 0.3
# 並列ワーカー数
NUM_WORKERS = 4
# バックエンドで選定するフレーム数上限
MAX_SELECTED_FRAMES = 20


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

    # ─── Phase 1: BBox追跡を4並列で実行 ───────────────────────
    logger.info("[register-worker] === Phase 1: BBox追跡 (%d枚, %d並列) ===", len(keys), NUM_WORKERS)
    t_phase1 = time.time()

    # 代表フレームは追跡不要（boxそのまま使う）
    other_keys = [k for k in keys if k != representative_key]

    def _track_one(key: str):
        """1フレームのBBox追跡。(key, img, tracked_box) or (key, None, None) を返す。"""
        try:
            img = _load_image(key)
            tracked_box = _track_bbox(img, box)
            return (key, img, tracked_box)
        except Exception as e:
            logger.warning("[register-worker] フレーム %s: 読み込み/追跡エラー: %s", key, e)
            return (key, None, None)

    tracked_frames = []
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
        results = list(pool.map(_track_one, other_keys))

    # 代表フレームを先頭に追加
    tracked_frames.append((representative_key, rep_img, box))
    for key, img, tracked_box in results:
        if img is not None and tracked_box is not None:
            tracked_frames.append((key, img, tracked_box))

    logger.info("[register-worker] Phase 1 完了: %d/%d 枚追跡成功, %.1f秒",
                len(tracked_frames), len(keys), time.time() - t_phase1)

    # ─── Phase 2: フレーム選定 (20枚) ─────────────────────────
    logger.info("[register-worker] === Phase 2: フレーム選定 ===")
    selected_frames = _select_best_frames(tracked_frames, representative_key, MAX_SELECTED_FRAMES)
    logger.info("[register-worker] Phase 2 完了: %d 枚選定", len(selected_frames))

    # ─── Phase 3: OCR + 埋め込みを4並列で実行 ─────────────────
    logger.info("[register-worker] === Phase 3: OCR + 埋め込み (%d枚, %d並列) ===",
                len(selected_frames), NUM_WORKERS)
    t_phase3 = time.time()

    # 新規登録か追加登録かを判定
    is_new_registration = msg.get("object_id") is None

    thumbnail_key = None

    def _embed_one(frame_tuple):
        """1フレームのクロップ→OCR→埋め込み。ベクトルリストを返す。"""
        key, img, frame_box = frame_tuple
        t_frame = time.time()

        # クロップ
        crop_img = crop_bbox(img, frame_box)

        # S3保存（再埋め込み原本）
        crop_key = f"crops/{object_id}/{key.rsplit('/', 1)[-1]}"
        s3_client.put_object(
            Bucket=IMAGE_BUCKET,
            Key=crop_key,
            Body=jpeg_bytes(crop_img),
            ContentType="image/jpeg",
        )

        # サムネイル（新規登録の代表画像のみ。追加登録時は既存サムネを維持）
        thumb_key = None
        if key == representative_key and is_new_registration:
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

        # 4方向埋め込み
        rotations = embed_rotations(crop_img, client=embed_client)

        # ベクトル構築
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

            vec_key = f"{object_id}__{key.rsplit('/', 1)[-1].rsplit('.', 1)[0]}__{deg}"
            vectors.append({
                "key": vec_key,
                "data": {"float32": vec.tolist()},
                "metadata": metadata,
            })

        elapsed = (time.time() - t_frame) * 1000
        logger.info("[register-worker] フレーム %s: OCR='%s', %d方向, %.1fms",
                    key.rsplit('/', 1)[-1], (raw_text or "")[:20], len(rotations), elapsed)

        return vectors, thumb_key

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
        embed_results = list(pool.map(_embed_one, selected_frames))

    # 結果集約
    all_vectors = []
    for vectors, thumb_key in embed_results:
        all_vectors.extend(vectors)
        if thumb_key:
            thumbnail_key = thumb_key

    logger.info("[register-worker] Phase 3 完了: %d ベクトル生成, %.1f秒",
                len(all_vectors), time.time() - t_phase3)

    # ─── Phase 4: DynamoDB VectorsTable に一括登録 ──────────────
    if not all_vectors:
        raise ValueError("有効なフレームがありませんでした")

    logger.info("[register-worker] === Phase 4: DynamoDB VectorsTable 登録 + DynamoDB ===")
    t_vec = time.time()
    _write_vectors_to_dynamodb(all_vectors, VECTORS_TABLE)
    logger.info("[register-worker] DynamoDB VectorsTable 全登録完了: %d ベクトル, %.1fms",
                len(all_vectors), (time.time() - t_vec) * 1000)

    # [S3V_DISABLED] DynamoDB Vector Search に移行 (2026-08-05)
    # 将来ベクトルストアを切り替える場合に再利用可能
    # ---
    # logger.info("[register-worker] === Phase 4: S3 Vectors 登録 + DynamoDB ===")
    # t_vec = time.time()
    # BATCH_SIZE = 100
    # for batch_start in range(0, len(all_vectors), BATCH_SIZE):
    #     batch = all_vectors[batch_start:batch_start + BATCH_SIZE]
    #     s3v_client.put_vectors(
    #         vectorBucketName=config.VECTOR_BUCKET,
    #         indexName=config.VECTOR_INDEX,
    #         vectors=batch,
    #     )
    #     logger.info("[register-worker] S3 Vectors バッチ登録: %d-%d / %d",
    #                 batch_start + 1, batch_start + len(batch), len(all_vectors))
    #
    # logger.info("[register-worker] S3 Vectors 全登録完了: %d ベクトル, %.1fms",
    #             len(all_vectors), (time.time() - t_vec) * 1000)
    # ---

    # ─── DynamoDB 物体テーブル登録 ─────────────────────────────
    initial_info_list = []
    if initial_info:
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
            # 既存レコード → サムネは変更しない、情報のみ追記
            if initial_info_list:
                objects_table.update_item(
                    Key={"object_id": object_id},
                    UpdateExpression="SET info = list_append(if_not_exists(info, :empty), :new_info)",
                    ExpressionAttributeValues={
                        ":new_info": initial_info_list,
                        ":empty": [],
                    },
                )
            logger.info("[register-worker] DynamoDB 既存レコード更新 (サムネ維持): object_id=%s", object_id)
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


# ─── フレーム選定ロジック ──────────────────────────────────────


def _select_best_frames(
    tracked_frames: list[tuple],
    representative_key: str,
    max_count: int,
) -> list[tuple]:
    """追跡成功フレームから最良の max_count 枚を選定する。

    選定基準:
    1. 代表フレームは必ず含める
    2. センタリングスコア: クロップ後に物体がBBox中心に近いほど高い
    3. 多様性: 既に選定したフレームと類似度が高すぎるものは後回し

    Args:
        tracked_frames: [(key, img, box), ...] 追跡成功フレーム一覧
        representative_key: 代表フレームのキー (必ず選定に含める)
        max_count: 選定する最大枚数

    Returns:
        選定されたフレームのリスト [(key, img, box), ...]
    """
    if len(tracked_frames) <= max_count:
        return tracked_frames

    # スコア計算: 物体のBBoxが画像中心に近いほど良い
    scored = []
    for key, img, frame_box in tracked_frames:
        score = _center_score(frame_box)
        scored.append((score, key, img, frame_box))

    # 代表フレームを先に確保
    selected = []
    remaining = []
    for score, key, img, frame_box in scored:
        if key == representative_key:
            selected.append((key, img, frame_box))
        else:
            remaining.append((score, key, img, frame_box))

    # スコア降順でソート
    remaining.sort(key=lambda x: x[0], reverse=True)

    # 貪欲法: スコアが高い順に、既選定と十分異なるものを追加
    for score, key, img, frame_box in remaining:
        if len(selected) >= max_count:
            break

        # 多様性チェック: 既選定フレームとBBoxの位置が近すぎないか
        if _is_diverse_enough(frame_box, [f[2] for f in selected]):
            selected.append((key, img, frame_box))

    # 多様性で弾かれて max_count に満たない場合はスコア順で埋める
    if len(selected) < max_count:
        selected_keys = {f[0] for f in selected}
        for score, key, img, frame_box in remaining:
            if len(selected) >= max_count:
                break
            if key not in selected_keys:
                selected.append((key, img, frame_box))

    return selected


def _center_score(box: list[int]) -> float:
    """BBoxの中心が画像中心 (500, 500) にどれだけ近いかのスコア (0〜1, 高いほど良い)。"""
    x1, y1, x2, y2 = box
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    # 中心からの距離 (最大 ~707)
    dist = ((cx - 500) ** 2 + (cy - 500) ** 2) ** 0.5
    max_dist = 707.0
    return 1.0 - (dist / max_dist)


def _is_diverse_enough(
    candidate_box: list[int],
    existing_boxes: list[list[int]],
    min_diff: float = 0.05,
) -> bool:
    """候補BBoxが既選定のBBoxと十分に異なるかチェック。

    BBox中心の差が min_diff (正規化) 以上ならOK。
    """
    cx = ((candidate_box[0] + candidate_box[2]) / 2) / 1000
    cy = ((candidate_box[1] + candidate_box[3]) / 2) / 1000

    for ebox in existing_boxes:
        ex = ((ebox[0] + ebox[2]) / 2) / 1000
        ey = ((ebox[1] + ebox[3]) / 2) / 1000
        diff = ((cx - ex) ** 2 + (cy - ey) ** 2) ** 0.5
        if diff < min_diff:
            return False
    return True


# ─── ユーティリティ ────────────────────────────────────────────


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
    boxes = detect_boxes(img, client=bedrock_client)

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
        return None

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



# ─── DynamoDB ベクトル書き込み ──────────────────────────────────


def _build_item(vec_entry: dict) -> dict:
    """ベクトルエントリから DynamoDB アイテム形式に変換。

    Args:
        vec_entry: Phase 3 で構築されたベクトル辞書
            - key: ベクトルキー (未使用、vector_id を独自構成)
            - data: {"float32": [float, ...]}
            - metadata: {object_id, source_file, rotation, label, text_tokens?}

    Returns:
        DynamoDB 形式のアイテム辞書
    """
    metadata = vec_entry["metadata"]
    object_id = metadata["object_id"]
    source_file = metadata["source_file"]
    rotation = metadata["rotation"]

    item = {
        "vector_id": {"S": f"{object_id}##{source_file}##{rotation}"},
        "object_id": {"S": object_id},
        "source_file": {"S": source_file},
        "rotation": {"N": str(rotation)},
        "vector": {"L": [{"N": str(v)} for v in vec_entry["data"]["float32"]]},
    }

    tokens = metadata.get("text_tokens")
    if tokens:
        item["text_tokens"] = {"SS": tokens}

    return item


def _write_vectors_to_dynamodb(all_vectors: list[dict], table_name: str):
    """DynamoDB BatchWriteItem でベクトルを書き込む (25件バッチ)。

    Args:
        all_vectors: Phase 3 で構築されたベクトルリスト
        table_name: VectorsTable のテーブル名
    """
    BATCH_SIZE = 25

    for batch_start in range(0, len(all_vectors), BATCH_SIZE):
        batch = all_vectors[batch_start:batch_start + BATCH_SIZE]
        request_items = {
            table_name: [
                {"PutRequest": {"Item": _build_item(v)}}
                for v in batch
            ]
        }
        resp = ddb_vectors_client.batch_write_item(RequestItems=request_items)

        # UnprocessedItems のリトライ
        unprocessed = resp.get("UnprocessedItems", {})
        while unprocessed:
            resp = ddb_vectors_client.batch_write_item(RequestItems=unprocessed)
            unprocessed = resp.get("UnprocessedItems", {})

        logger.info("[register-worker] DynamoDB バッチ登録: %d-%d / %d",
                    batch_start + 1, batch_start + len(batch), len(all_vectors))
