"""評価スクリプト: query_dataset/ を回して本人/他人ペアのスコア分布を出す。

フォルダ名が登録済みobject_idと一致 → 正解=そのid(本人ペア)
フォルダ名が unknown_ 始まり → 正解=未登録(他人ペア)
"""

import csv
from pathlib import Path

import boto3
import numpy as np

from eden.clients import (
    get_bedrock_client,
    get_bedrock_embed_client,
    get_bedrock_text_client,
    get_s3vectors_client,
)
from eden.image_util import load_image
from eden.search import search_image

PROFILE = "kkmtyyz_mfa"

session = boto3.Session(profile_name=PROFILE)
bbox_client = get_bedrock_client(session=session)
embed_client = get_bedrock_embed_client(session=session)
text_client = get_bedrock_text_client(session=session)
s3v_client = get_s3vectors_client(session=session)


def search_image_file(path):
    """画像ファイルから検索(クロップ込み)"""
    img = load_image(path)
    return search_image(
        img,
        bbox_client=bbox_client,
        embed_client=embed_client,
        s3v_client=s3v_client,
        text_client=text_client,
    )


def main():
    rows = []
    for obj_dir in sorted(Path("query_dataset").iterdir()):
        if not obj_dir.is_dir():
            continue
        truth = None if obj_dir.name.startswith("unknown_") else obj_dir.name

        for f in sorted(obj_dir.glob("*.jpg")) + sorted(obj_dir.glob("*.png")):
            print(f"query: {f}")
            r = search_image_file(str(f))
            correct_id = (truth is not None) and (r.object_id == truth)
            rows.append(
                {
                    "file": str(f),
                    "truth": truth or "UNREGISTERED",
                    "predicted": r.object_id or "",
                    "verdict": r.verdict,
                    "sim": r.sim,
                    "sim_raw": r.sim_raw,
                    "raw_top1_id": r.raw_top1_id or "",
                    "rank_changed": r.rank_changed,
                    "margin": r.margin,
                    "votes": r.votes,
                    "text_hit": r.text_hit,
                    "id_correct": correct_id,
                }
            )
            print(
                f"  -> {r.verdict} pred={r.object_id} "
                f"sim={r.sim} margin={r.margin} votes={r.votes}"
            )

    # CSV保存
    with open("eval_result.csv", "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    # ==== 分布サマリ ====
    pos = [r for r in rows if r["truth"] != "UNREGISTERED"]
    neg = [r for r in rows if r["truth"] == "UNREGISTERED"]

    def stats(name, rs, key):
        if not rs:
            return
        vals = np.array([r[key] for r in rs], dtype=float)
        print(
            f"  {name:20s} {key}: "
            f"min={vals.min():.3f} p25={np.percentile(vals, 25):.3f} "
            f"med={np.median(vals):.3f} p75={np.percentile(vals, 75):.3f} "
            f"max={vals.max():.3f}"
        )

    print("\n========== スコア分布 ==========")
    for key in ("sim", "margin", "votes"):
        stats("本人ペア(登録済み)", pos, key)
        stats("他人ペア(未登録)", neg, key)
        print()

    # ==== 判定マトリクス ====
    print("========== 判定結果 ==========")
    print(f"{'':16s} green   yellow  red")
    for name, rs in (("登録済み", pos), ("未登録", neg)):
        g = sum(1 for r in rs if r["verdict"] == "green")
        y = sum(1 for r in rs if r["verdict"] == "yellow")
        rd = sum(1 for r in rs if r["verdict"] == "red")
        print(f"{name:16s} {g:5d}   {y:5d}   {rd:4d}")

    # 危険な誤り: 未登録なのに緑(データ汚染に直結)
    danger = [r for r in neg if r["verdict"] == "green"]
    # 緑なのにID間違い(別物体に情報追加されるパターン)
    wrong_green = [r for r in pos if r["verdict"] == "green" and not r["id_correct"]]

    print(f"\n!! 未登録→緑(偽陽性): {len(danger)}件")
    for r in danger:
        print(f"   {r['file']} -> {r['predicted']} sim={r['sim']}")
    print(f"!! 緑だけどID誤り: {len(wrong_green)}件")
    for r in wrong_green:
        print(f"   {r['file']} truth={r['truth']} pred={r['predicted']}")

    # ==== 文字一致の効果 ====
    print("========== 文字一致の効果 ==========")
    text_queries = [r for r in rows if r.get("text_hit")]
    print(f"文字一致が発生したクエリ: {len(text_queries)}/{len(rows)}件")

    rank_flips = [r for r in rows if r.get("rank_changed")]
    print(f"文字ブーストでtop1が入れ替わった: {len(rank_flips)}件")
    for r in rank_flips:
        good = "○正解に矯正" if r["id_correct"] else "×誤爆"
        print(f"   {good} {r['file']}: {r['raw_top1_id']} -> {r['predicted']}")

    # 判定境界への影響: ブースト無しだったら緑条件(sim)を割ってたケース
    saved = [
        r
        for r in rows
        if r["verdict"] == "green" and r["sim"] >= 0.85 and r["sim_raw"] < 0.85
    ]
    print(f"文字ブーストが無ければ緑を逃してたクエリ: {len(saved)}件")
    for r in saved:
        print(f"   {r['file']}: sim_raw={r['sim_raw']} -> sim={r['sim']}")

    print("\n=> eval_result.csv を保存。分布の谷を見て閾値を調整してね")


if __name__ == "__main__":
    main()
