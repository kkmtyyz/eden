"""ベンチマーク実行CLIツール。

S3 Vectors と DynamoDB Vector Search を並行してベンチマークし、
レイテンシ・精度・コストの比較レポートを出力する。

使用例:
    # 基本実行 (10イテレーション)
    python -m experiments.dynamodb_vector_benchmark.benchmark --query-dir dataset/query

    # イテレーション数指定
    python -m experiments.dynamodb_vector_benchmark.benchmark --query-dir dataset/query -n 50

    # ウォームアップ無効化
    python -m experiments.dynamodb_vector_benchmark.benchmark --query-dir dataset/query --no-warmup

    # コスト見積もり付き
    python -m experiments.dynamodb_vector_benchmark.benchmark --query-dir dataset/query --cost-estimate
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import boto3

# eden パッケージへのパス追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eden import config
from eden.clients import (
    get_bedrock_client,
    get_bedrock_embed_client,
    get_bedrock_text_client,
    get_s3vectors_client,
)
from eden.crop import crop_center_object
from eden.embedding import embed_image
from eden.image_util import load_image
from eden.ocr import extract_text, normalize_text
from eden.search import SearchResult, compute_scores, judge_verdict, query_vectors

from .ddb_search import (
    DDB_INDEX_NAME,
    DDB_TABLE_NAME,
    check_vector_index_active,
    query_vectors_ddb,
)
from .report import (
    print_accuracy_report,
    print_cost_estimate,
    print_latency_table,
)


def _run_single_query(
    img_path: Path,
    *,
    bbox_client,
    embed_client,
    text_client,
    s3v_client,
    ddb_client,
    table_name: str,
    index_name: str,
) -> dict[str, Any]:
    """1枚の画像に対して両エンジンのクエリを実行する。

    Args:
        img_path: クエリ画像パス
        bbox_client: BBox検出用クライアント
        embed_client: Embedding用クライアント
        text_client: テキスト抽出用クライアント
        s3v_client: S3 Vectors クライアント
        ddb_client: DynamoDB クライアント
        table_name: DynamoDBテーブル名
        index_name: ベクトルインデックス名

    Returns:
        {
            "query_id": str,
            "embedding_ms": float,
            "s3v_latency_ms": float,
            "ddb_latency_ms": float,
            "s3v_result": SearchResult,
            "ddb_result": SearchResult,
            "s3v_top_k_ids": set[str],
            "ddb_top_k_ids": set[str],
        }
    """
    # 画像読み込み & クロップ
    img = load_image(str(img_path))
    crop_img = crop_center_object(img, client=bbox_client)

    # テキスト抽出 (両エンジン共通)
    raw_text = extract_text(crop_img, client=text_client)
    query_tokens = normalize_text(raw_text)

    # 埋め込み (両エンジン共通)
    t0 = time.perf_counter()
    vec = embed_image(crop_img, client=embed_client)
    embedding_ms = (time.perf_counter() - t0) * 1000
    vec_list = vec.tolist()

    # --- S3 Vectors クエリ ---
    t0 = time.perf_counter()
    s3v_hits = query_vectors(vec_list, client=s3v_client)
    s3v_latency_ms = (time.perf_counter() - t0) * 1000

    # S3 Vectors 結果処理
    if s3v_hits:
        s3v_hits_scored, s3v_text_matched = compute_scores(s3v_hits, query_tokens)
        s3v_judge = judge_verdict(s3v_hits_scored, s3v_text_matched)
        s3v_result = SearchResult(
            verdict=s3v_judge["verdict"],
            object_id=s3v_judge["object_id"],
            sim=s3v_judge["sim"],
            sim_raw=s3v_judge["sim_raw"],
            raw_top1_id=s3v_judge["raw_top1_id"],
            rank_changed=s3v_judge["rank_changed"],
            margin=s3v_judge["margin"],
            votes=s3v_judge["votes"],
            text_hit=s3v_judge["text_hit"],
            query_tokens=query_tokens,
        )
        s3v_top_k_ids = {h["object_id"] for h in s3v_hits_scored}
    else:
        s3v_result = SearchResult(verdict="red", reason="no_hits", query_tokens=query_tokens)
        s3v_top_k_ids = set()

    # --- DynamoDB Vector Search クエリ ---
    t0 = time.perf_counter()
    ddb_hits = query_vectors_ddb(
        vec_list,
        client=ddb_client,
        table_name=table_name,
        index_name=index_name,
    )
    ddb_latency_ms = (time.perf_counter() - t0) * 1000

    # DynamoDB 結果処理
    if ddb_hits:
        ddb_hits_scored, ddb_text_matched = compute_scores(ddb_hits, query_tokens)
        ddb_judge = judge_verdict(ddb_hits_scored, ddb_text_matched)
        ddb_result = SearchResult(
            verdict=ddb_judge["verdict"],
            object_id=ddb_judge["object_id"],
            sim=ddb_judge["sim"],
            sim_raw=ddb_judge["sim_raw"],
            raw_top1_id=ddb_judge["raw_top1_id"],
            rank_changed=ddb_judge["rank_changed"],
            margin=ddb_judge["margin"],
            votes=ddb_judge["votes"],
            text_hit=ddb_judge["text_hit"],
            query_tokens=query_tokens,
        )
        ddb_top_k_ids = {h["object_id"] for h in ddb_hits_scored}
    else:
        ddb_result = SearchResult(verdict="red", reason="no_hits", query_tokens=query_tokens)
        ddb_top_k_ids = set()

    return {
        "query_id": img_path.stem,
        "embedding_ms": embedding_ms,
        "s3v_latency_ms": s3v_latency_ms,
        "ddb_latency_ms": ddb_latency_ms,
        "s3v_result": s3v_result,
        "ddb_result": ddb_result,
        "s3v_top_k_ids": s3v_top_k_ids,
        "ddb_top_k_ids": ddb_top_k_ids,
    }


def run_benchmark(
    query_dir: Path,
    *,
    iterations: int = 10,
    warmup: bool = True,
    session: boto3.Session | None = None,
    table_name: str = DDB_TABLE_NAME,
    index_name: str = DDB_INDEX_NAME,
) -> dict[str, Any]:
    """ベンチマークを実行する。

    ウォームアップ (1イテレーション) を除外したN回のイテレーションで
    レイテンシを計測する。

    Args:
        query_dir: クエリ画像ディレクトリ
        iterations: 計測イテレーション数
        warmup: ウォームアップ実行するか (1回目を除外)
        session: boto3セッション
        table_name: DynamoDBテーブル名
        index_name: ベクトルインデックス名

    Returns:
        ベンチマーク結果辞書
    """
    s = session or boto3.Session()

    # クライアント初期化
    bbox_client = get_bedrock_client(session=s)
    embed_client = get_bedrock_embed_client(session=s)
    text_client = get_bedrock_text_client(session=s)
    s3v_client = get_s3vectors_client(session=s)
    ddb_client = s.client("dynamodb", region_name=config.VECTOR_REGION)

    # インデックス状態確認
    if not check_vector_index_active(table_name, index_name, client=ddb_client):
        print(f"警告: DynamoDB ベクトルインデックスがACTIVEではありません: {table_name}/{index_name}")
        print("  --create-table で migrate.py を実行してください。")
        sys.exit(1)

    # クエリ画像一覧
    query_images = sorted(
        p for p in query_dir.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    if not query_images:
        print(f"クエリ画像が見つかりません: {query_dir}")
        sys.exit(1)

    print(f"\n=== ベンチマーク設定 ===")
    print(f"  クエリ画像数: {len(query_images)}")
    print(f"  イテレーション数: {iterations}")
    print(f"  ウォームアップ: {'あり (1回)' if warmup else 'なし'}")
    print(f"  S3 Vectors: {config.VECTOR_BUCKET}/{config.VECTOR_INDEX}")
    print(f"  DynamoDB: {table_name}/{index_name}")

    # ウォームアップ (結果は破棄)
    if warmup and query_images:
        print("\n  ウォームアップ実行中...")
        _run_single_query(
            query_images[0],
            bbox_client=bbox_client,
            embed_client=embed_client,
            text_client=text_client,
            s3v_client=s3v_client,
            ddb_client=ddb_client,
            table_name=table_name,
            index_name=index_name,
        )
        print("  => ウォームアップ完了")

    # ベンチマーク実行
    print(f"\n  ベンチマーク実行中 ({iterations}イテレーション)...")
    s3v_latencies: list[float] = []
    ddb_latencies: list[float] = []
    accuracy_results: list[dict[str, Any]] = []

    for i in range(iterations):
        # 各イテレーションで全画像を順番にクエリ
        for img_path in query_images:
            result = _run_single_query(
                img_path,
                bbox_client=bbox_client,
                embed_client=embed_client,
                text_client=text_client,
                s3v_client=s3v_client,
                ddb_client=ddb_client,
                table_name=table_name,
                index_name=index_name,
            )

            s3v_latencies.append(result["s3v_latency_ms"])
            ddb_latencies.append(result["ddb_latency_ms"])

            # 精度比較は最初のイテレーションのみ記録
            if i == 0:
                accuracy_results.append({
                    "query_id": result["query_id"],
                    "s3v_verdict": result["s3v_result"].verdict,
                    "ddb_verdict": result["ddb_result"].verdict,
                    "s3v_object_id": result["s3v_result"].object_id,
                    "ddb_object_id": result["ddb_result"].object_id,
                    "s3v_top_k_ids": result["s3v_top_k_ids"],
                    "ddb_top_k_ids": result["ddb_top_k_ids"],
                })

        print(f"    イテレーション {i + 1}/{iterations} 完了")

    return {
        "s3v_latencies": s3v_latencies,
        "ddb_latencies": ddb_latencies,
        "accuracy_results": accuracy_results,
        "num_queries": len(query_images),
        "iterations": iterations,
    }


def main():
    """CLIエントリーポイント。"""
    parser = argparse.ArgumentParser(
        description="DynamoDB Vector Search ベンチマーク実行"
    )
    parser.add_argument(
        "--query-dir",
        type=Path,
        required=True,
        help="クエリ画像ディレクトリ",
    )
    parser.add_argument(
        "-n", "--iterations",
        type=int,
        default=10,
        help="計測イテレーション数 (デフォルト: 10)",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="ウォームアップを無効化する",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS プロファイル名",
    )
    parser.add_argument(
        "--table-name",
        default=DDB_TABLE_NAME,
        help=f"DynamoDBテーブル名 (デフォルト: {DDB_TABLE_NAME})",
    )
    parser.add_argument(
        "--index-name",
        default=DDB_INDEX_NAME,
        help=f"ベクトルインデックス名 (デフォルト: {DDB_INDEX_NAME})",
    )
    parser.add_argument(
        "--cost-estimate",
        action="store_true",
        help="コスト見積もりも出力する",
    )
    parser.add_argument(
        "--num-vectors",
        type=int,
        default=1000,
        help="コスト見積もり用: 格納ベクトル数 (デフォルト: 1000)",
    )
    parser.add_argument(
        "--monthly-queries",
        type=int,
        default=10000,
        help="コスト見積もり用: 月間クエリ数 (デフォルト: 10000)",
    )

    args = parser.parse_args()

    if not args.query_dir.is_dir():
        print(f"エラー: クエリディレクトリが見つかりません: {args.query_dir}")
        sys.exit(1)

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()

    # ベンチマーク実行
    results = run_benchmark(
        args.query_dir,
        iterations=args.iterations,
        warmup=not args.no_warmup,
        session=session,
        table_name=args.table_name,
        index_name=args.index_name,
    )

    # レポート出力
    print_latency_table(results["s3v_latencies"], results["ddb_latencies"])
    print_accuracy_report(results["accuracy_results"])

    if args.cost_estimate:
        print_cost_estimate(
            args.num_vectors,
            args.monthly_queries,
        )

    print("\n=== ベンチマーク完了 ===")


if __name__ == "__main__":
    main()
