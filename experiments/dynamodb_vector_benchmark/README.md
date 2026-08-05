# DynamoDB Vector Search ベンチマークツール

S3 Vectors と DynamoDB Vector Search の性能比較ベンチマークを実施し、
レイテンシ・精度・コストの観点で両エンジンを評価するためのツール群です。

## 概要

このツールは以下の3ステップで使用します:

1. **テーブル作成 & データ移行** (`migrate.py`): DynamoDB テーブルをベクトルインデックス付きで作成し、S3 Vectors のデータを移行
2. **ベンチマーク実行** (`benchmark.py`): 両エンジンに同一クエリを投げてレイテンシ・精度を比較
3. **レポート確認**: レイテンシ統計、精度比較 (Jaccard係数)、コスト見積もりを自動出力

## セットアップ

### 前提条件

- Python 3.11+
- boto3 がインストール済み
- AWS認証情報が設定済み (プロファイル or 環境変数)
- S3 Vectors にデータが登録済み (`experiments/05_register_objects.py` で事前登録)

### ディレクトリ構成

```
experiments/dynamodb_vector_benchmark/
├── __init__.py       # パッケージ初期化
├── utils.py          # ユーティリティ (vector_id構築・パース)
├── ddb_search.py     # DynamoDB Vector Search クエリ関数
├── report.py         # レポート出力 (レイテンシ・精度・コスト)
├── migrate.py        # テーブル作成 & データ移行CLI
├── benchmark.py      # ベンチマーク実行CLI
└── README.md         # このファイル
```

## 使い方

### 1. テーブル作成

```bash
# デフォルト設定で作成
python -m experiments.dynamodb_vector_benchmark.migrate --create-table

# プロファイル指定
python -m experiments.dynamodb_vector_benchmark.migrate --profile my_profile --create-table

# カスタムテーブル名
python -m experiments.dynamodb_vector_benchmark.migrate --table-name my-table --create-table
```

テーブル作成後、ベクトルインデックスが ACTIVE になるまで自動で待機します（最大10分程度）。

### 2. データ移行

```bash
# S3 Vectors → DynamoDB にデータ移行
python -m experiments.dynamodb_vector_benchmark.migrate --migrate

# テーブル作成とデータ移行を同時に実行
python -m experiments.dynamodb_vector_benchmark.migrate --create-table --migrate

# プロファイル指定 + バッチサイズ指定
python -m experiments.dynamodb_vector_benchmark.migrate --profile my_profile --migrate --batch-size 25
```

### 3. ベンチマーク実行

```bash
# 基本実行 (10イテレーション)
python -m experiments.dynamodb_vector_benchmark.benchmark --query-dir dataset/query

# イテレーション数を変更
python -m experiments.dynamodb_vector_benchmark.benchmark --query-dir dataset/query -n 50

# ウォームアップなし
python -m experiments.dynamodb_vector_benchmark.benchmark --query-dir dataset/query --no-warmup

# コスト見積もり付き
python -m experiments.dynamodb_vector_benchmark.benchmark --query-dir dataset/query --cost-estimate

# フルオプション
python -m experiments.dynamodb_vector_benchmark.benchmark \
  --query-dir dataset/query \
  --profile my_profile \
  -n 30 \
  --cost-estimate \
  --num-vectors 5000 \
  --monthly-queries 100000
```

### 4. クリーンアップ

```bash
# テーブル削除
python -m experiments.dynamodb_vector_benchmark.migrate --delete-table
```

## 出力例

### レイテンシ比較

```
=== レイテンシ比較 ===
指標       S3 Vectors (ms)   DynamoDB (ms)     差分 (ms)     比率
------------------------------------------------------------------------
平均              45.23           38.91        -6.32    0.86x
P50               42.10           36.50        -5.60    0.87x
P95               68.30           55.20       -13.10    0.81x
P99               82.50           62.10       -20.40    0.75x
最小              35.20           30.10        -5.10    0.86x
最大              95.00           78.00       -17.00    0.82x
------------------------------------------------------------------------
サンプル数: S3 Vectors=100, DynamoDB=100
```

### 精度比較

```
=== 精度比較 ===
クエリ数: 10
verdict一致率: 9/10 (90.0%)
top-1 object_id一致率: 10/10 (100.0%)
top-k Jaccard係数 (平均): 0.9250
top-k Jaccard係数 (最小): 0.8000
top-k Jaccard係数 (最大): 1.0000
```

### コスト見積もり

```
=== コスト見積もり (月間・概算) ===
格納ベクトル数: 5,000
月間クエリ数: 100,000
ベクトル次元: 1024

--- S3 Vectors ---
  ストレージ: $0.0004 (0.0191 GB)
  クエリ: $0.0400
  合計: $0.0404

--- DynamoDB Vector Search ---
  ストレージ: $0.0050 (0.0201 GB)
  クエリ: $0.0025
  合計: $0.0075
```

## 設計上のポイント

### vector_id フォーマット

```
{object_id}##{source_file}##{rotation}
```

例: `aws_mug##dataset/aws_mug/photo1.jpg##90`

### DDB レスポンスの正規化

DynamoDB Vector Search のレスポンスを `eden.search.query_vectors` 互換形式に変換し、
`eden.search.compute_scores` / `judge_verdict` をそのまま再利用しています。

```python
# DynamoDB レスポンス (生)
[{"Item": {"vector_id": {"S": "..."}, ...}, "Distance": 0.15}, ...]

# 正規化後 (eden.search互換)
[{"distance": 0.15, "metadata": {"object_id": "aws_mug", "text_tokens": [...]}}, ...]
```

### ウォームアップ

最初の1イテレーションはウォームアップとして実行し、結果から除外します。
これにより、コールドスタートの影響を排除した安定した計測が可能です。

### text_tokens の取り扱い

- DynamoDB の SS 型 (String Set) で格納
- 空リストの場合はアイテムに含めない (DynamoDB の空セット制約対応)
- 正規化時に `SS` → `list` に変換

## 環境変数

以下の環境変数で設定を上書きできます（`eden/config.py` 参照）:

| 変数名 | デフォルト値 | 説明 |
|--------|-------------|------|
| `EDEN_VECTOR_REGION` | `ap-northeast-1` | ベクトル検索リージョン |
| `EDEN_VECTOR_BUCKET` | `my-s3-vector-bucket` | S3 Vectors バケット名 |
| `EDEN_VECTOR_INDEX` | `my-s3-vector-index` | S3 Vectors インデックス名 |
| `EDEN_TOP_K` | `20` | 検索結果取得件数 |
| `EDEN_EMBED_DIM` | `1024` | ベクトル次元数 |

## 注意事項

- DynamoDB Vector Search はプレビュー段階の機能であり、API仕様・料金が変更される可能性があります
- ベンチマーク結果はネットワーク状況やリージョンにより変動します
- コスト見積もりは概算であり、実際の課金は AWS 公式ドキュメントを参照してください
- `migrate.py` の `--delete-table` はデータを完全に削除します。本番環境では使用しないでください
