"""DynamoDB SearchVectors のレスポンス構造を確認するスクリプト。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import boto3
from experiments.dynamodb_vector_benchmark.settings import (
    aws_profile,
    ddb_table,
    ddb_index,
    ddb_region,
)

def main():
    profile = aws_profile()
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    client = session.client("dynamodb", region_name=ddb_region())

    table = ddb_table()
    index = ddb_index()

    print(f"Table: {table}")
    print(f"Index: {index}")
    print(f"Profile: {profile}")
    print(f"Region: {ddb_region()}")
    print()

    # ダミーベクトル (全部0.01) で検索
    test_vec = [{"N": "0.01"}] * 1024

    print("Calling search_vectors...")
    resp = client.search_vectors(
        TableName=table,
        IndexName=index,
        SearchVector=test_vec,
        TopK=5,
    )

    # ResponseMetadata を除外して表示
    resp.pop("ResponseMetadata", None)

    print("\n=== Response Structure ===")
    print(json.dumps(resp, indent=2, default=str))

    # トップレベルのキーを表示
    print(f"\n=== Top-level keys: {list(resp.keys())} ===")

    # ヒットがあればその構造も表示
    for key in resp:
        if isinstance(resp[key], list) and len(resp[key]) > 0:
            print(f"\n=== First item in '{key}': ===")
            print(json.dumps(resp[key][0], indent=2, default=str))

if __name__ == "__main__":
    main()
