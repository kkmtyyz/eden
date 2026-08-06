"""運用ツール: DynamoDB VectorsTable の作成 & S3 Vectors からのデータ移行。

手動実行用 Lambda。API Gateway には接続せず、コンソールやCLIから直接 invoke する。

使い方:
    # テーブル作成 + データ移行
    aws lambda invoke --function-name <FN> --payload '{"confirm": true}' out.json

    # テーブル作成のみ
    aws lambda invoke --function-name <FN> --payload '{"confirm": true, "create_only": true}' out.json

    # データ移行のみ (テーブルが既に存在する場合)
    aws lambda invoke --function-name <FN> --payload '{"confirm": true, "migrate_only": true}' out.json

オプション:
    confirm (bool): true を渡さないと実行しない（安全装置）
    create_only (bool): テーブル作成のみ実行 (デフォルト: false)
    migrate_only (bool): データ移行のみ実行 (デフォルト: false)
    batch_size (int): DynamoDB バッチ書き込みサイズ (デフォルト: 25, 最大: 25)
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
    """DynamoDB VectorsTable の作成 & S3 Vectors からのデータ移行。

    event に {"confirm": true} を渡さないと実行しない（安全装置）。
    """
    if not event.get("confirm"):
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "Safety check: pass {\"confirm\": true} to execute.",
            }),
        }

    create_only = event.get("create_only", False)
    migrate_only = event.get("migrate_only", False)
    batch_size = min(event.get("batch_size", 25), 25)

    results = {}

    try:
        # Phase 1: テーブル作成 (migrate_only でなければ)
        if not migrate_only:
            table_created = _create_table_with_vector_index()
            results["table_created"] = table_created

        # Phase 2: データ移行 (create_only でなければ)
        if not create_only:
            if not VECTOR_BUCKET:
                return {
                    "statusCode": 400,
                    "body": json.dumps({
                        "error": "EDEN_VECTOR_BUCKET environment variable is not set. Cannot migrate data.",
                    }),
                }

            vectors = _fetch_all_s3_vectors()
            results["vectors_fetched"] = len(vectors)

            if vectors:
                written = _write_to_dynamodb(vectors, batch_size)
                results["vectors_written"] = written
            else:
                results["vectors_written"] = 0

        results["message"] = "Operation completed successfully."
        return {
            "statusCode": 200,
            "body": json.dumps(results),
        }

    except Exception as e:
        logger.error("[migrate] 失敗: %s", str(e), exc_info=True)
        results["error"] = str(e)
        return {
            "statusCode": 500,
            "body": json.dumps(results),
        }


def _create_table_with_vector_index() -> bool:
    """DynamoDB テーブルを VectorIndex 付きで作成する。

    既にテーブルが存在する場合はスキップする。

    Returns:
        True: 新規作成, False: 既存テーブルが存在
    """
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

    # テーブルが ACTIVE になるまで待機
    logger.info("[migrate] テーブルがACTIVEになるのを待機中...")
    waiter = ddb_client.get_waiter("table_exists")
    waiter.wait(
        TableName=VECTORS_TABLE,
        WaiterConfig={"Delay": 5, "MaxAttempts": 60},
    )

    # ベクトルインデックスの ACTIVE 待機
    logger.info("[migrate] ベクトルインデックスがACTIVEになるのを待機中...")
    for _ in range(120):
        resp = ddb_client.describe_table(TableName=VECTORS_TABLE)
        vector_indexes = resp["Table"].get("VectorIndexes", [])
        for idx in vector_indexes:
            if idx["IndexName"] == DDB_INDEX_NAME:
                status = idx.get("IndexStatus", "UNKNOWN")
                if status == "ACTIVE":
                    logger.info("[migrate] インデックスACTIVE: %s", DDB_INDEX_NAME)
                    return True
                elif status in ("FAILED", "DELETING"):
                    raise RuntimeError(f"Vector index creation failed: {status}")
        time.sleep(5)

    raise TimeoutError("ベクトルインデックスのアクティベーションがタイムアウトしました")


def _fetch_all_s3_vectors() -> list[dict]:
    """S3 Vectors から全ベクトルデータ (メタデータ + ベクトル値) を取得する。"""
    logger.info("[migrate] S3 Vectors 取得開始: bucket=%s, index=%s", VECTOR_BUCKET, VECTOR_INDEX)
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

    logger.info("[migrate] S3 Vectors 取得完了: %d 件", len(all_vectors))
    return all_vectors


def _write_to_dynamodb(vectors: list[dict], batch_size: int) -> int:
    """取得したベクトルデータを DynamoDB VectorsTable にバッチ書き込みする。"""
    logger.info("[migrate] DynamoDB 書き込み開始: %d 件, バッチサイズ=%d", len(vectors), batch_size)
    written = 0

    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i + batch_size]
        put_requests = []

        for vec in batch:
            item = _build_item(vec)
            if item:
                put_requests.append({"PutRequest": {"Item": item}})

        if not put_requests:
            continue

        # バッチ書き込み (UnprocessedItems リトライ)
        request_items = {VECTORS_TABLE: put_requests}
        while request_items:
            resp = ddb_client.batch_write_item(RequestItems=request_items)
            request_items = resp.get("UnprocessedItems", {})
            if request_items:
                time.sleep(0.5)

        written += len(put_requests)

        if written % 100 == 0 or i + batch_size >= len(vectors):
            logger.info("[migrate] DynamoDB 書き込み: %d/%d 件", written, len(vectors))

    logger.info("[migrate] DynamoDB 書き込み完了: %d 件", written)
    return written


def _build_item(vec: dict) -> dict | None:
    """S3 Vectors のベクトルエントリを DynamoDB アイテム形式に変換する。"""
    metadata = vec.get("metadata", {})
    object_id = metadata.get("object_id")
    source_file = metadata.get("source_file", "")
    rotation = metadata.get("rotation", 0)

    vector_data = vec.get("data", {}).get("float32", [])
    if not vector_data or not object_id:
        logger.warning("[migrate] ベクトルデータまたは object_id が欠落: key=%s", vec.get("key"))
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
