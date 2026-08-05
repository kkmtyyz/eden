"""DynamoDB テーブル作成 & S3 Vectors からのデータ移行CLIツール。

使用例:
    # テーブル作成
    python -m experiments.dynamodb_vector_benchmark.migrate --create-table

    # データ移行
    python -m experiments.dynamodb_vector_benchmark.migrate --migrate

    # テーブル作成 + データ移行
    python -m experiments.dynamodb_vector_benchmark.migrate --create-table --migrate

    # カスタムプロファイル
    python -m experiments.dynamodb_vector_benchmark.migrate --profile my_profile --migrate
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import boto3

# eden パッケージへのパス追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eden import config
from eden.clients import get_s3vectors_client

from .ddb_search import DDB_INDEX_NAME, DDB_TABLE_NAME
from .utils import build_vector_id

# デフォルト設定
DEFAULT_TABLE_NAME = DDB_TABLE_NAME
DEFAULT_INDEX_NAME = DDB_INDEX_NAME
VECTOR_DIM = config.EMBED_DIM  # 1024


def create_table(
    table_name: str = DEFAULT_TABLE_NAME,
    index_name: str = DEFAULT_INDEX_NAME,
    vector_dim: int = VECTOR_DIM,
    *,
    client=None,
    session: boto3.Session | None = None,
    wait: bool = True,
) -> None:
    """DynamoDB テーブルをベクトルインデックス付きで作成する。

    Args:
        table_name: テーブル名
        index_name: ベクトルインデックス名
        vector_dim: ベクトル次元数
        client: DynamoDB クライアント
        session: boto3セッション
        wait: テーブルがACTIVEになるまで待機するか
    """
    if client is None:
        s = session or boto3.Session()
        client = s.client("dynamodb", region_name=config.VECTOR_REGION)

    print(f"テーブル作成開始: {table_name}")
    print(f"  インデックス: {index_name}")
    print(f"  ベクトル次元: {vector_dim}")

    try:
        client.create_table(
            TableName=table_name,
            AttributeDefinitions=[
                {"AttributeName": "vector_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "vector_id", "KeyType": "HASH"},
            ],
            BillingMode="PAY_PER_REQUEST",
            VectorIndexes=[
                {
                    "IndexName": index_name,
                    "Dimension": vector_dim,
                    "VectorAttribute": "vector",
                    "DistanceMetric": "cosine",
                    "ProjectionType": "ALL",
                }
            ],
        )
        print("  => create_table リクエスト送信完了")
    except client.exceptions.ResourceInUseException:
        print(f"  => テーブル '{table_name}' は既に存在します。スキップします。")
        return

    if wait:
        print("  テーブルがACTIVEになるのを待機中...")
        waiter = client.get_waiter("table_exists")
        waiter.wait(
            TableName=table_name,
            WaiterConfig={"Delay": 5, "MaxAttempts": 60},
        )
        print("  => テーブルACTIVE")

        # ベクトルインデックスのアクティベーション待機
        print("  ベクトルインデックスがACTIVEになるのを待機中...")
        for _ in range(120):
            resp = client.describe_table(TableName=table_name)
            vector_indexes = resp["Table"].get("VectorIndexes", [])
            for idx in vector_indexes:
                if idx["IndexName"] == index_name:
                    status = idx.get("IndexStatus", "UNKNOWN")
                    if status == "ACTIVE":
                        print(f"  => インデックスACTIVE: {index_name}")
                        return
                    elif status in ("FAILED", "DELETING"):
                        print(f"  => インデックスエラー: {status}")
                        raise RuntimeError(
                            f"Vector index creation failed: {status}"
                        )
            time.sleep(5)
        raise TimeoutError("ベクトルインデックスのアクティベーションがタイムアウトしました")


def _fetch_all_s3_vectors(
    s3v_client,
    bucket: str | None = None,
    index: str | None = None,
) -> list[dict]:
    """S3 Vectors から全ベクトルデータを取得する。

    Args:
        s3v_client: S3 Vectors クライアント
        bucket: バケット名
        index: インデックス名

    Returns:
        ベクトルデータリスト
    """
    bucket = bucket or config.VECTOR_BUCKET
    index = index or config.VECTOR_INDEX

    all_vectors = []
    next_token = None

    while True:
        kwargs: dict = {
            "vectorBucketName": bucket,
            "indexName": index,
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

        print(f"  取得中... {len(all_vectors)}件")

    return all_vectors


def migrate_data(
    table_name: str = DEFAULT_TABLE_NAME,
    *,
    ddb_client=None,
    s3v_client=None,
    session: boto3.Session | None = None,
    batch_size: int = 25,
) -> int:
    """S3 Vectors のデータを DynamoDB にマイグレーションする。

    Args:
        table_name: 移行先テーブル名
        ddb_client: DynamoDB クライアント
        s3v_client: S3 Vectors クライアント
        session: boto3セッション
        batch_size: バッチ書き込みサイズ (最大25)

    Returns:
        マイグレーションしたベクトル数
    """
    s = session or boto3.Session()
    if ddb_client is None:
        ddb_client = s.client("dynamodb", region_name=config.VECTOR_REGION)
    if s3v_client is None:
        s3v_client = get_s3vectors_client(session=s)

    print("\n=== データ移行開始 ===")
    print(f"  S3 Vectors: {config.VECTOR_BUCKET}/{config.VECTOR_INDEX}")
    print(f"  DynamoDB: {table_name}")

    # S3 Vectors からデータ取得
    print("\n  S3 Vectorsからデータ取得中...")
    vectors = _fetch_all_s3_vectors(s3v_client)
    print(f"  => 取得完了: {len(vectors)}件")

    if not vectors:
        print("  移行対象データなし")
        return 0

    # DynamoDB にバッチ書き込み
    print(f"\n  DynamoDBへの書き込み開始 (バッチサイズ: {batch_size})...")
    written = 0

    for i in range(0, len(vectors), batch_size):
        batch = vectors[i : i + batch_size]
        put_requests = []

        for vec in batch:
            metadata = vec.get("metadata", {})
            object_id = metadata.get("object_id", "unknown")
            source_file = metadata.get("source_file", "")
            rotation = metadata.get("rotation", 0)
            text_tokens = metadata.get("text_tokens", [])

            # ベクトルデータの取得
            vector_data = vec.get("data", {}).get("float32", [])
            if not vector_data:
                continue

            # vector_id 構築
            vector_id = build_vector_id(object_id, source_file, int(rotation))

            # DynamoDB アイテム構築
            item: dict = {
                "vector_id": {"S": vector_id},
                "object_id": {"S": object_id},
                "source_file": {"S": source_file},
                "rotation": {"N": str(rotation)},
                "vector": {"VF": vector_data},
            }

            # text_tokens は非空の場合のみ
            if text_tokens:
                item["text_tokens"] = {"SS": text_tokens}

            put_requests.append({"PutRequest": {"Item": item}})

        if put_requests:
            # バッチ書き込み (未処理アイテムのリトライ含む)
            request_items = {table_name: put_requests}
            while request_items:
                resp = ddb_client.batch_write_item(RequestItems=request_items)
                request_items = resp.get("UnprocessedItems", {})
                if request_items:
                    time.sleep(0.5)  # スロットリング対策

            written += len(put_requests)

        if (i + batch_size) % 100 == 0 or i + batch_size >= len(vectors):
            print(f"    書き込み済み: {written}/{len(vectors)}")

    print(f"\n  => 移行完了: {written}件")
    return written


def delete_table(
    table_name: str = DEFAULT_TABLE_NAME,
    *,
    client=None,
    session: boto3.Session | None = None,
) -> None:
    """DynamoDB テーブルを削除する (クリーンアップ用)。

    Args:
        table_name: テーブル名
        client: DynamoDB クライアント
        session: boto3セッション
    """
    if client is None:
        s = session or boto3.Session()
        client = s.client("dynamodb", region_name=config.VECTOR_REGION)

    try:
        client.delete_table(TableName=table_name)
        print(f"テーブル削除リクエスト送信: {table_name}")
    except client.exceptions.ResourceNotFoundException:
        print(f"テーブルが見つかりません: {table_name}")


def main():
    """CLIエントリーポイント。"""
    parser = argparse.ArgumentParser(
        description="DynamoDB Vector Search ベンチマーク: テーブル作成 & データ移行"
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS プロファイル名",
    )
    parser.add_argument(
        "--table-name",
        default=DEFAULT_TABLE_NAME,
        help=f"DynamoDBテーブル名 (デフォルト: {DEFAULT_TABLE_NAME})",
    )
    parser.add_argument(
        "--index-name",
        default=DEFAULT_INDEX_NAME,
        help=f"ベクトルインデックス名 (デフォルト: {DEFAULT_INDEX_NAME})",
    )
    parser.add_argument(
        "--create-table",
        action="store_true",
        help="テーブルを作成する",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="S3 Vectorsからデータを移行する",
    )
    parser.add_argument(
        "--delete-table",
        action="store_true",
        help="テーブルを削除する (クリーンアップ)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="バッチ書き込みサイズ (デフォルト: 25, 最大25)",
    )

    args = parser.parse_args()

    if not any([args.create_table, args.migrate, args.delete_table]):
        parser.print_help()
        sys.exit(1)

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    ddb_client = session.client("dynamodb", region_name=config.VECTOR_REGION)

    if args.delete_table:
        delete_table(args.table_name, client=ddb_client)
        return

    if args.create_table:
        create_table(
            args.table_name,
            args.index_name,
            client=ddb_client,
            session=session,
        )

    if args.migrate:
        s3v_client = get_s3vectors_client(session=session)
        migrate_data(
            args.table_name,
            ddb_client=ddb_client,
            s3v_client=s3v_client,
            session=session,
            batch_size=min(args.batch_size, 25),
        )


if __name__ == "__main__":
    main()
