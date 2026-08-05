"""レポート出力: レイテンシ統計・精度比較 (Jaccard係数)・コスト見積もり。"""

from __future__ import annotations

import statistics
from typing import Any


def _percentile(data: list[float], pct: float) -> float:
    """パーセンタイル値を計算する (線形補間)。

    Args:
        data: ソート済みデータ
        pct: パーセンタイル (0〜100)

    Returns:
        パーセンタイル値
    """
    if not data:
        return 0.0
    n = len(data)
    k = (pct / 100) * (n - 1)
    f = int(k)
    c = f + 1 if f + 1 < n else f
    d = k - f
    return data[f] + d * (data[c] - data[f])


def compute_latency_stats(latencies_ms: list[float]) -> dict[str, float]:
    """レイテンシ統計値を計算する。

    Args:
        latencies_ms: レイテンシのリスト (ミリ秒)

    Returns:
        {"mean": float, "p50": float, "p95": float, "p99": float, "min": float, "max": float}
    """
    if not latencies_ms:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0}

    sorted_data = sorted(latencies_ms)
    return {
        "mean": statistics.mean(sorted_data),
        "p50": _percentile(sorted_data, 50),
        "p95": _percentile(sorted_data, 95),
        "p99": _percentile(sorted_data, 99),
        "min": sorted_data[0],
        "max": sorted_data[-1],
    }


def print_latency_table(
    s3v_latencies_ms: list[float],
    ddb_latencies_ms: list[float],
) -> None:
    """S3 Vectors と DynamoDB のレイテンシ比較テーブルを出力する。

    Args:
        s3v_latencies_ms: S3 Vectors の各イテレーションのレイテンシ (ms)
        ddb_latencies_ms: DynamoDB の各イテレーションのレイテンシ (ms)
    """
    s3v_stats = compute_latency_stats(s3v_latencies_ms)
    ddb_stats = compute_latency_stats(ddb_latencies_ms)

    header = f"{'指標':<8} {'S3 Vectors (ms)':>16} {'DynamoDB (ms)':>16} {'差分 (ms)':>12} {'比率':>8}"
    separator = "-" * len(header)

    print("\n=== レイテンシ比較 ===")
    print(header)
    print(separator)

    for key, label in [
        ("mean", "平均"),
        ("p50", "P50"),
        ("p95", "P95"),
        ("p99", "P99"),
        ("min", "最小"),
        ("max", "最大"),
    ]:
        s3v_val = s3v_stats[key]
        ddb_val = ddb_stats[key]
        diff = ddb_val - s3v_val
        ratio = ddb_val / s3v_val if s3v_val > 0 else float("inf")
        print(f"{label:<8} {s3v_val:>16.2f} {ddb_val:>16.2f} {diff:>12.2f} {ratio:>7.2f}x")

    print(separator)
    print(f"サンプル数: S3 Vectors={len(s3v_latencies_ms)}, DynamoDB={len(ddb_latencies_ms)}")


def compute_jaccard(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard係数を計算する。

    J(A, B) = |A ∩ B| / |A ∪ B|

    Args:
        set_a: 集合A
        set_b: 集合B

    Returns:
        Jaccard係数 (0.0〜1.0)。両方空の場合は1.0を返す。
    """
    if not set_a and not set_b:
        return 1.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def print_accuracy_report(
    results: list[dict[str, Any]],
) -> None:
    """精度比較レポートを出力する。

    各クエリについてS3 VectorsとDynamDBの結果を比較し、
    verdict一致率・top-1 object_id一致率・top-k Jaccard係数を報告する。

    Args:
        results: ベンチマーク結果リスト。各要素:
            {
                "query_id": str,
                "s3v_verdict": str,
                "ddb_verdict": str,
                "s3v_object_id": str | None,
                "ddb_object_id": str | None,
                "s3v_top_k_ids": set[str],
                "ddb_top_k_ids": set[str],
            }
    """
    if not results:
        print("\n=== 精度比較 ===")
        print("結果データなし")
        return

    total = len(results)
    verdict_match = sum(1 for r in results if r["s3v_verdict"] == r["ddb_verdict"])
    object_match = sum(
        1 for r in results
        if r.get("s3v_object_id") and r["s3v_object_id"] == r.get("ddb_object_id")
    )
    jaccard_scores = [
        compute_jaccard(r.get("s3v_top_k_ids", set()), r.get("ddb_top_k_ids", set()))
        for r in results
    ]

    print("\n=== 精度比較 ===")
    print(f"クエリ数: {total}")
    print(f"verdict一致率: {verdict_match}/{total} ({verdict_match / total * 100:.1f}%)")
    print(f"top-1 object_id一致率: {object_match}/{total} ({object_match / total * 100:.1f}%)")
    print(f"top-k Jaccard係数 (平均): {statistics.mean(jaccard_scores):.4f}")
    if len(jaccard_scores) > 1:
        print(f"top-k Jaccard係数 (最小): {min(jaccard_scores):.4f}")
        print(f"top-k Jaccard係数 (最大): {max(jaccard_scores):.4f}")

    # 不一致の詳細
    mismatches = [r for r in results if r["s3v_verdict"] != r["ddb_verdict"]]
    if mismatches:
        print(f"\n--- verdict不一致 ({len(mismatches)}件) ---")
        for r in mismatches[:10]:  # 最大10件表示
            print(
                f"  {r['query_id']}: S3V={r['s3v_verdict']} vs DDB={r['ddb_verdict']}"
            )
        if len(mismatches) > 10:
            print(f"  ... 他 {len(mismatches) - 10}件")


def print_cost_estimate(
    num_vectors: int,
    monthly_queries: int,
    vector_dim: int = 1024,
) -> None:
    """月間コスト見積もりを出力する。

    ※ 料金はプレビュー時点の公開情報に基づく概算。
    実際の課金は AWS 公式ドキュメントを参照すること。

    Args:
        num_vectors: 格納ベクトル数
        monthly_queries: 月間クエリ数
        vector_dim: ベクトル次元数 (デフォルト 1024)
    """
    # S3 Vectors 概算コスト (参考値)
    # ストレージ: $0.023/GB/月 (S3 Standard相当)
    # クエリ: S3 Vectors APIリクエスト料金
    s3v_storage_gb = num_vectors * vector_dim * 4 / (1024**3)  # float32 = 4bytes
    s3v_storage_cost = s3v_storage_gb * 0.023
    s3v_query_cost = monthly_queries * 0.0000004  # $0.40/100万リクエスト相当

    # DynamoDB 概算コスト (参考値)
    # オンデマンド: 書き込み $1.25/100万WRU, 読み取り $0.25/100万RRU
    # ストレージ: $0.25/GB/月
    # ベクトル検索追加コスト: プレビュー中は要確認
    ddb_storage_gb = num_vectors * (vector_dim * 4 + 200) / (1024**3)  # ベクトル+属性
    ddb_storage_cost = ddb_storage_gb * 0.25
    # search_vectors のRRU消費は1リクエストあたり大きめ (仮: 100 RRU/query)
    ddb_query_cost = monthly_queries * 100 * 0.00000025  # $0.25/100万RRU

    print("\n=== コスト見積もり (月間・概算) ===")
    print(f"格納ベクトル数: {num_vectors:,}")
    print(f"月間クエリ数: {monthly_queries:,}")
    print(f"ベクトル次元: {vector_dim}")
    print()

    print("--- S3 Vectors ---")
    print(f"  ストレージ: ${s3v_storage_cost:.4f} ({s3v_storage_gb:.4f} GB)")
    print(f"  クエリ: ${s3v_query_cost:.4f}")
    print(f"  合計: ${s3v_storage_cost + s3v_query_cost:.4f}")
    print()

    print("--- DynamoDB Vector Search ---")
    print(f"  ストレージ: ${ddb_storage_cost:.4f} ({ddb_storage_gb:.4f} GB)")
    print(f"  クエリ: ${ddb_query_cost:.4f}")
    print(f"  合計: ${ddb_storage_cost + ddb_query_cost:.4f}")
    print()

    print("※ 概算値。実際の料金は AWS 公式ドキュメントを参照してください。")
    print("※ DynamoDB Vector Search はプレビュー中のため料金体系が変更される可能性があります。")
