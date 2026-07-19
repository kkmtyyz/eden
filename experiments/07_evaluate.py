# 07_evaluate.py
"""評価: query_dataset/ を回して本人/他人ペアのスコア分布を出す
フォルダ名が登録済みobject_idと一致 → 正解=そのid(本人ペア)
フォルダ名が unknown_ 始まり → 正解=未登録(他人ペア)
"""

import csv
from pathlib import Path

import numpy as np
from importlib import import_module

reg = import_module("05_register_objects")
search = import_module("06_search")


def main():
    rows = []
    for obj_dir in sorted(Path("query_dataset").iterdir()):
        if not obj_dir.is_dir():
            continue
        truth = None if obj_dir.name.startswith("unknown_") else obj_dir.name

        for f in sorted(obj_dir.glob("*.jpg")) + sorted(obj_dir.glob("*.png")):
            print(f"query: {f}")
            r = search.search_image_file(f)
            correct_id = (truth is not None) and (r.get("object_id") == truth)
            rows.append(
                {
                    "file": str(f),
                    "truth": truth or "UNREGISTERED",
                    "predicted": r.get("object_id", ""),
                    "verdict": r["verdict"],
                    "sim": r.get("sim", 0.0),
                    "sim_raw": r.get("sim_raw", 0.0),
                    "raw_top1_id": r.get("raw_top1_id", ""),
                    "rank_changed": r.get("rank_changed", False),
                    "margin": r.get("margin", 0.0),
                    "votes": r.get("votes", 0),
                    "text_hit": r.get("text_hit", False),
                    "id_correct": correct_id,
                }
            )
            print(
                f"  -> {r['verdict']} pred={r.get('object_id')} "
                f"sim={r.get('sim')} margin={r.get('margin')} votes={r.get('votes')}"
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
        if r["verdict"] == "green" and r["sim"] >= 0.80 and r["sim_raw"] < 0.80
    ]
    print(f"文字ブーストが無ければ緑を逃してたクエリ: {len(saved)}件")
    for r in saved:
        print(f"   {r['file']}: sim_raw={r['sim_raw']} -> sim={r['sim']}")

    print("\n=> eval_result.csv を保存。分布の谷を見て 06_search.py のTHを調整してね")


if __name__ == "__main__":
    main()
