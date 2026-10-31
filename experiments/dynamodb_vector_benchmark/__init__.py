"""DynamoDB Vector Search ベンチマークツール。

S3 Vectors と DynamoDB Vector Search の性能比較ベンチマークを実施し、
レイテンシ・精度・コストの観点で両エンジンを評価するためのツール群。

モジュール構成:
    - utils: ベクトルID生成・パース、DDBアイテム構築ユーティリティ
    - ddb_search: DynamoDB Vector Search クエリ関数
    - report: レイテンシ統計・精度比較・コスト見積もりレポート出力
    - migrate: テーブル作成・S3 Vectorsからのデータ移行CLIツール
    - benchmark: ベンチマーク実行CLIツール
"""

__version__ = "0.1.0"
