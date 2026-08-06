"""運用ツール: ベクトルデータの移行。

以下の3モードをサポート:
1. DynamoDB → S3 Vectors へコピー (restore モード)
2. S3 Vectors → DynamoDB VectorsTable へコピー (migrate モード)
3. DynamoDB VectorsTable の作成 (create-table モード)

手動実行用 Lambda。API Gateway には接続せず、コンソールやCLIから直接 invoke する。

使い方:
    # DynamoDB (ベンチマークテーブル) → S3 Vectors へ復元
    aws lambda invoke --function-name <FN> --payload '{"confirm": true, "mode": "restore", "source_table": "eden-vector-benchmark"}' out.json

    # S3 Vectors → 新 DynamoDB VectorsTable へ移行 (テーブル作成込み)
    aws lambda invoke --function-name <FN> --payload '{"confirm": true, "mode": "migrate"}' out.json

    # DynamoDB VectorsTable 作成のみ
    aws lambda invoke --function-name <FN> --payload '{"confirm": true, "mode": "create-table"}' out.json
"""

import json
import logging
import os
import time

import boto3

from eden import config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 環境変数
VECTORS_TABLE = os.environ.get("EDEN_VECTORS_TABLE", config.VECTORS_TABLE)
VECTOR_BUCKET = os.environ.get("EDEN_VECTOR_BUCKET", "")
VECTOR_INDEX = os.environ.get("EDEN_VECTOR_INDEX", "eden-index")

# クライアント
s3v_client = boto3.client("s3vectors", region_name=config.VECTOR_REGION)
ddb_client = boto3.client("dynamodb", region_name=config.VECTOR_REGION)

# 定数
_SEPARATOR = "##"
VECTOR_DIM = 1024
DDB_INDEX_NAME = "eden-vector-index"


def handler(event, context):
    """ベクトルデータの移行を実行する。

    event に {"confirm": true} を渡さないと実行しない（安全装置）。
    """
    if not event.get("confirm"):
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": 'Safety check: pass {"confirm": true} to execute.',
            }),
        }

    mode = event.get("mode", "migrate")

    try:
        if mode == "restore":
            # DynamoDB → S3 Vectors へ復元
            source_table = event.get("source_table", "eden-vector-benchmark")
            return _handle_restore(source_table)
        elif mode == "migrate":
            # S3 Vectors → DynamoDB VectorsTable
            batch_size = min(event.get("batch_size", 25), 25)
            return _handle_migrate(batch_size)
        elif mode == "create-table":
            # テーブル作成のみ
            return _handle_create_table()
        else:
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": f'Unknown mode: {mode}. Use "restore", "migrate", or "create-table".',
                }),
            }
    except Exception as e:
        logger.error("[migrate] 失敗: %s", str(e), exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


# ═══════════════════════════════════════════════════════════════
# Mode: restore (DynamoDB → S3 Vectors)
# ═══════════════════════════════════════════════════════════════


def _handle_restore(source_table: str) -> dict:
    """DynamoDB テーブルから S3 Vectors へベクトルデータを復元する。"""
    logger.info("[restore] === DynamoDB → S3 Vectors 復元開始 ===")
    logger.info("[restore] 読み込み元: table=%s", source_table)
    logger.info("[restore] 書き込み先: bucket=%s, index=%s", VECTOR_BUCKET, VECTOR_INDEX)

    if not VECTOR_BUCKET:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "EDEN_VECTOR_BUCKET is not set."}),
        }

    # Phase 1: DynamoDB から全アイテムを Scan
    items = _scan_all_ddb_items(source_table)
    logger.info("[restore] DynamoDB 取得完了: %d 件", len(items))

    if not items:
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "No items to restore.",
                "items_fetched": 0,
                "vectors_written": 0,
            }),
        }

    # Phase 2: S3 Vectors に put_vectors (100件バッチ)
    written = _write_to_s3v(items)
    logger.info("[restore] S3 Vectors 書き込み完了: %d 件", written)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Restore completed successfully.",
            "items_fetched": len(items),
            "vectors_written": written,
        }),
    }


def _scan_all_ddb_items(table_name: str) -> list[dict]:
    """DynamoDB テーブルの全アイテムを Scan で取得する。"""
    all_items = []
    scan_kwargs = {"TableName": table_name}

    while True:
        resp = ddb_client.scan(**scan_kwargs)
        items = resp.get("Items", [])
        all_items.extend(items)

        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

        if len(all_items) % 100 == 0:
            logger.info("[restore] DynamoDB Scan 中... %d 件", len(all_items))

    return all_items


def _write_to_s3v(items: list[dict]) -> int:
    """DynamoDB アイテムを S3 Vectors に書き込む (100件バッチ)。"""
    BATCH_SIZE = 100
    written = 0

    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        vectors = []

        for item in batch:
            vec_entry = _ddb_item_to_s3v_vector(item)
            if vec_entry:
                vectors.append(vec_entry)

        if not vectors:
            continue

        s3v_client.put_vectors(
            vectorBucketName=VECTOR_BUCKET,
            indexName=VECTOR_INDEX,
            vectors=vectors,
        )
        written += len(vectors)

        if written % 100 == 0 or i + BATCH_SIZE >= len(items):
            logger.info("[restore] S3 Vectors 書き込み: %d/%d 件", written, len(items))

    return written


def _ddb_item_to_s3v_vector(item: dict) -> dict | None:
    """DynamoDB アイテムを S3 Vectors の put_vectors 形式に変換する。

    DynamDB アイテム形式:
        vector_id: {S: "{object_id}##{source_file}##{rotation}"}
        object_id: {S: "..."}
        source_file: {S: "..."}
        rotation: {N: "0"}
        vector: {L: [{N: "0.123"}, ...]}  または {VF: [0.123, ...]}
        text_tokens: {SS: ["token1", ...]}  (optional)

    S3 Vectors put_vectors 形式:
        key: "{object_id}__{filename}__{rotation}"
        data: {"float32": [float, ...]}
        metadata: {object_id, rotation, label, source_file, text_tokens?}
    """
    object_id = item.get("object_id", {}).get("S", "")
    source_file = item.get("source_file", {}).get("S", "")
    rotation = item.get("rotation", {}).get("N", "0")

    if not object_id:
        return None

    # ベクトルデータ取得 (L of N 形式 or VF 形式)
    vector_data = []
    if "vector" in item:
        vec_attr = item["vector"]
        if "L" in vec_attr:
            vector_data = [float(v["N"]) for v in vec_attr["L"]]
        elif "VF" in vec_attr:
            vector_data = vec_attr["VF"]

    if not vector_data:
        return None

    # S3 Vectors のキー形式: {object_id}__{filename_without_ext}__{rotation}
    filename = source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0] if source_file else "unknown"
    vec_key = f"{object_id}__{filename}__{rotation}"

    # メタデータ構築
    metadata: dict = {
        "object_id": object_id,
        "rotation": int(rotation),
        "label": object_id,
        "source_file": source_file,
    }

    text_tokens = item.get("text_tokens", {}).get("SS", [])
    if text_tokens:
        metadata["text_tokens"] = list(text_tokens)

    return {
        "key": vec_key,
        "data": {"float32": vector_data},
        "metadata": metadata,
    }


# ═══════════════════════════════════════════════════════════════
# Mode: migrate (S3 Vectors → DynamoDB)
# ═══════════════════════════════════════════════════════════════


def _handle_migrate(batch_size: int) -> dict:
    """S3 Vectors → DynamoDB VectorsTable へ移行する。テーブルがなければ作成する。"""
    logger.info("[migrate] === S3 Vectors → DynamoDB 移行開始 ===")
    logger.info("[migrate] 移行元: bucket=%s, index=%s", VECTOR_BUCKET, VECTOR_INDEX)
    logger.info("[migrate] 移行先: table=%s", VECTORS_TABLE)

    if not VECTOR_BUCKET:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "EDEN_VECTOR_BUCKET is not set."}),
        }

    # テーブル作成 (既存ならスキップ)
    _create_table_with_vector_index()

    # S3 Vectors からデータ取得
    vectors = _fetch_all_s3_vectors()
    logger.info("[migrate] S3 Vectors 取得完了: %d 件", len(vectors))

    if not vectors:
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "No vectors to migrate.",
                "vectors_fetched": 0,
                "vectors_written": 0,
            }),
        }

    # DynamoDB に書き込み
    written = _write_to_dynamodb(vectors, batch_size)
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Migration completed successfully.",
            "vectors_fetched": len(vectors),
            "vectors_written": written,
        }),
    }


# ═══════════════════════════════════════════════════════════════
# Mode: create-table
# ═══════════════════════════════════════════════════════════════


def _handle_create_table() -> dict:
    """DynamoDB VectorsTable の作成のみ実行する。"""
    created = _create_table_with_vector_index()
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Table created." if created else "Table already exists.",
            "table_created": created,
        }),
    }


# ═══════════════════════════════════════════════════════════════
# 共通ユーティリティ
# ═══════════════════════════════════════════════════════════════


def _create_table_with_vector_index() -> bool:
    """DynamoDB テーブルを VectorIndex 付きで作成する。既存ならスキップ。"""
    logger.info("[migrate] テーブル作成開始: %s", VECTORS_TABLE)

    try:
        ddb_client.create_table(
            TableName=VECTORS_TABLE,
            AttributeDefinitions=[
                {"AttributeName": "vector_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "vector_id", "KeyType": "HASH"},
            ],
            BillingMode="PAY_PER_REQUEST",
            VectorIndexes=[
                {
                    "IndexName": DDB_INDEX_NAME,
                    "Dimensions": VECTOR_DIM,
                    "DistanceFunction": "COSINE",
                    "VectorAttribute": {"AttributeName": "vector"},
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        )
        logger.info("[migrate] create_table リクエスト送信完了")
    except ddb_client.exceptions.ResourceInUseException:
        logger.info("[migrate] テーブル '%s' は既に存在します。スキップ。", VECTORS_TABLE)
        return False

    # テーブル ACTIVE 待機
    waiter = ddb_client.get_waiter("table_exists")
    waiter.wait(TableName=VECTORS_TABLE, WaiterConfig={"Delay": 5, "MaxAttempts": 60})

    # ベクトルインデックス ACTIVE 待機
    logger.info("[migrate] ベクトルインデックス ACTIVE 待機中...")
    for _ in range(120):
        resp = ddb_client.describe_table(TableName=VECTORS_TABLE)
        for idx in resp["Table"].get("VectorIndexes", []):
            if idx["IndexName"] == DDB_INDEX_NAME:
                status = idx.get("IndexStatus", "UNKNOWN")
                if status == "ACTIVE":
                    logger.info("[migrate] インデックスACTIVE")
                    return True
                elif status in ("FAILED", "DELETING"):
                    raise RuntimeError(f"Vector index failed: {status}")
        time.sleep(5)

    raise TimeoutError("Vector index activation timeout")


def _fetch_all_s3_vectors() -> list[dict]:
    """S3 Vectors から全ベクトルデータを取得する。"""
    all_vectors = []
    next_token = None

    while True:
        kwargs: dict = {
            "vectorBucketName": VECTOR_BUCKET,
            "indexName": VECTOR_INDEX,
            "returnData": True,
            "returnMetadata": True,
        }
        if next_token:
            kwargs["nextToken"] = next_token

        resp = s3v_client.list_vectors(**kwargs)
        vectors = resp.get("vectors", [])
        all_vectors.extend(vectors)

        next_token = resp.get("nextToken")
        if not next_token:
            break

        logger.info("[migrate] S3 Vectors 取得中... %d 件", len(all_vectors))

    return all_vectors


def _write_to_dynamodb(vectors: list[dict], batch_size: int) -> int:
    """S3 Vectors データを DynamoDB に書き込む。"""
    written = 0

    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i + batch_size]
        put_requests = []

        for vec in batch:
            item = _s3v_to_ddb_item(vec)
            if item:
                put_requests.append({"PutRequest": {"Item": item}})

        if not put_requests:
            continue

        request_items = {VECTORS_TABLE: put_requests}
        while request_items:
            resp = ddb_client.batch_write_item(RequestItems=request_items)
            request_items = resp.get("UnprocessedItems", {})
            if request_items:
                time.sleep(0.5)

        written += len(put_requests)

    logger.info("[migrate] DynamoDB 書き込み完了: %d 件", written)
    return written


def _s3v_to_ddb_item(vec: dict) -> dict | None:
    """S3 Vectors エントリを DynamoDB アイテム形式に変換する。"""
    metadata = vec.get("metadata", {})
    object_id = metadata.get("object_id")
    source_file = metadata.get("source_file", "")
    rotation = metadata.get("rotation", 0)

    vector_data = vec.get("data", {}).get("float32", [])
    if not vector_data or not object_id:
        return None

    vector_id = f"{object_id}{_SEPARATOR}{source_file}{_SEPARATOR}{rotation}"

    item: dict = {
        "vector_id": {"S": vector_id},
        "object_id": {"S": object_id},
        "source_file": {"S": source_file},
        "rotation": {"N": str(rotation)},
        "vector": {"L": [{"N": str(v)} for v in vector_data]},
    }

    text_tokens = metadata.get("text_tokens")
    if text_tokens:
        item["text_tokens"] = {"SS": text_tokens}

    return item
