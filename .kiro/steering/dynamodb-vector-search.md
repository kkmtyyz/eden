<!-- DynamoDB Vector Search 移行ガイド -->
<!-- 2026-08-05 GA 当日の検証結果に基づく -->

# DynamoDB Vector Search 移行ガイド

## 概要

2026-08-05 に GA した DynamoDB のネイティブベクトル検索機能。
既存の S3 Vectors から移行することで、ベクトルと物体メタデータを同一テーブルで管理でき、
ゴーストベクトル問題やデータ同期パイプラインが不要になる。

## ベンチマーク結果サマリー

| 指標 | S3 Vectors | DynamoDB VS | 差分 |
|------|-----------|------------|------|
| P50 レイテンシ | 135ms | 100ms | **-26%** |
| P95 レイテンシ | 150ms | 121ms | **-20%** |
| verdict 一致率 | - | - | 100% |
| top-1 object_id 一致率 | - | - | 100% |
| Jaccard@top-20 | - | - | 1.0 |

データ: 188ベクトル (1024次元, cosine), 9クエリ画像, 3イテレーション

## API 仕様 (boto3)

### テーブル作成 (create_table)

```python
client.create_table(
    TableName="eden-vector-benchmark",
    AttributeDefinitions=[
        {"AttributeName": "vector_id", "AttributeType": "S"},
    ],
    KeySchema=[
        {"AttributeName": "vector_id", "KeyType": "HASH"},
    ],
    BillingMode="PAY_PER_REQUEST",
    VectorIndexes=[
        {
            "IndexName": "eden-vector-index",
            "Dimensions": 1024,
            "VectorAttribute": {"AttributeName": "vector"},
            "DistanceFunction": "COSINE",
            "Projection": {"ProjectionType": "ALL"},
        }
    ],
)
```

**正式なパラメータ名 (ハマりポイント):**
- `Dimensions` (複数形。`Dimension` ではない)
- `DistanceFunction` (`DistanceMetric` ではない)
- `VectorAttribute`: dict型 `{"AttributeName": "vector"}` (`VectorAttributeType` は不要)
- `Projection`: dict型 `{"ProjectionType": "ALL"}` (文字列ではない)

### ベクトルデータの格納

DynamoDB にベクトルを保存する際は **List of Numbers (L型)** で格納する:

```python
item = {
    "vector_id": {"S": "object_id##source_file##rotation"},
    "object_id": {"S": "xxx"},
    "vector": {"L": [{"N": str(v)} for v in embedding]},  # ← これ
    "text_tokens": {"SS": ["token1", "token2"]},  # 存在する場合のみ
}
```

**ハマりポイント:**
- `"VF"` 型は存在しない。`{"VF": [...]}` はエラーになる
- `{"L": [{"N": "0.123"}, ...]}` の形式で格納する
- text_tokens は非空の場合のみ SS 型で格納 (DynamoDB は空セットを許容しない)

### 検索 (search_vectors)

```python
resp = client.search_vectors(
    TableName="eden-vector-benchmark",
    IndexName="eden-vector-index",
    SearchVector=[{"N": str(v)} for v in query_vector],  # L of N 形式
    TopK=20,
)
```

**正式なパラメータ名:**
- `SearchVector` (`QueryVector` ではない)
- SearchVector の各要素は `{"N": "0.123"}` 形式 (float のリストではない)
- `TopK` (これは想定通り)

### レスポンス構造

```python
{
    "SearchResults": [  # "Items" や "Hits" ではない
        {
            "Item": {
                "vector_id": {"S": "..."},
                "object_id": {"S": "..."},
                "text_tokens": {"SS": ["...", "..."]},
                "rotation": {"N": "90"},
                "source_file": {"S": "..."},
            },
            "Score": 0.0751  # cosine distance (低い = 類似)
        },
        ...
    ]
}
```

**Score の解釈 (重要):**
- COSINE: 0 (同一) 〜 2 (正反対)。**低いほど類似**
- EUCLIDEAN: 0 〜 ∞。低いほど類似
- DOT_PRODUCT: 高いほど類似 (唯一の例外)

eden の既存ロジック (`sim = 1 - distance`) に渡すには `distance = Score` でそのまま使う。

## CDK / CloudFormation 対応状況

- GA 当日時点で CDK の L2 コンストラクト (TableV2) は VectorIndexes 未対応
- CloudFormation の `AWS::DynamoDB::Table` は `VectorIndexes` パラメータをサポート
- Amplify (CDK) で使う場合は L1 の `CfnTable` で直接指定するか、CustomResource を使う

## テーブル設計 (Eden 移行時の方針)

```
── ベクトルテーブル (新設) ──
PK: vector_id (String) = "{object_id}##{source_file}##{rotation}"
属性:
  - vector: List<Number> (1024次元) ← VectorIndex 対象
  - object_id: String
  - source_file: String
  - rotation: Number (0, 90, 180, 270)
  - text_tokens: StringSet (OCR テキストがある場合のみ)

VectorIndex:
  - IndexName: eden-vector-index
  - Dimensions: 1024
  - DistanceFunction: COSINE
  - Projection: ALL

── 物体メタテーブル (既存 Objects テーブルを流用) ──
PK: object_id (String)
属性:
  - thumbnail_key: String
  - info: List<Map> (コメントリスト)
```

### なぜテーブルを分けるか

- 1物体あたり最大80ベクトル (20フレーム × 4回転)
- コメントを80アイテムに複製するのは冗長
- 検索後に object_id で GetItem すれば良い (現在と同じフロー)

## 移行時の注意点

1. **boto3 バージョン**: GA 当日以降のバージョンが必須。`pip install --upgrade boto3 botocore`
2. **ベクトルインデックス作成後の待機**: ACTIVE になるまで数分かかる場合がある
3. **CloudFormation ドリフト**: 手動削除したインデックスは CFn が再作成しない。名前を変えるか CFn スタック外で管理
4. **S3 Vectors eventual consistency**: 削除直後はクエリで古い結果が返ることがある (DynamoDB 移行で解消)
5. **SearchConditionExpression**: HASH 属性は等値のみ、INLINE_FILTER 属性は比較・範囲演算子が使える

## 実験ツールの場所

```
experiments/dynamodb_vector_benchmark/
├── config.yaml      # 環境設定 (バケット名、テーブル名、プロファイル等)
├── settings.py      # config.yaml 読み込みモジュール
├── migrate.py       # テーブル作成 + データ移行 CLI
├── benchmark.py     # ベンチマーク実行 CLI
├── ddb_search.py    # SearchVectors ラッパー + レスポンス正規化
├── report.py        # 比較レポート表示
├── response_check.py # レスポンス構造確認用スクリプト
└── utils.py         # vector_id 生成/パース
```
