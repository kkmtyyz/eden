# 06_search.py
"""検索ロジック: クロップ → ベクトル化 → query_vectors → マージン+合議制+文字一致 → 緑/黄/赤"""

import json
from difflib import SequenceMatcher

import boto3
import numpy as np

# 05_register_objects.py から共通関数を再利用
from importlib import import_module

reg = import_module(
    "05_register_objects"
)  # ファイル名が数字始まりなのでimport_module経由

PROFILE = "kkmtyyz_mfa"
VECTOR_REGION = "ap-northeast-1"
VECTOR_BUCKET = "my-s3-vector-bucket"
VECTOR_INDEX = "my-s3-vector-index"

TOP_K = 20

# ==== 閾値(暫定値。07_evaluate.py の分布を見て調整する) ====
TH = {
    #"green_sim": 0.80,  # top1類似度がこれ以上
    "green_sim": 0.85,  # top1類似度がこれ以上
    "green_margin": 0.05,  # top1と別object_idのtop2の差がこれ以上
    "green_votes": 5,  # top-k内の同一object_id出現数がこれ以上
    "yellow_sim": 0.70,  # ここ未満は問答無用で赤
    "yellow_votes": 2,
    "text_boost": 0.05,  # 文字一致時に類似度に足すブースト
    "text_match_ratio": 0.7,  # 編集距離ベースの一致率閾値
    "text_mismatch_penalty": 0.05,  # 両者に文字が有るのに不一致なら減点
}

session = boto3.Session(profile_name=PROFILE)
s3v_client = session.client("s3vectors", region_name=VECTOR_REGION)


def token_match(query_tokens, cand_tokens, ratio_th):
    """部分一致 or 編集距離でトークン同士の一致を判定"""
    for q in query_tokens:
        for c in cand_tokens:
            if q in c or c in q:
                return True
            if SequenceMatcher(None, q, c).ratio() >= ratio_th:
                return True
    return False


def search_object(img):
    """クロップ済み画像1枚を検索して判定結果を返す"""
    # 文字抽出(クエリ側は1回だけ、回転なし)
    raw_text = reg.extract_text(img)
    query_tokens = reg.normalize_text(raw_text)

    vec = reg.embed_image(img)
    resp = s3v_client.query_vectors(
        vectorBucketName=VECTOR_BUCKET,
        indexName=VECTOR_INDEX,
        topK=TOP_K,
        queryVector={"float32": vec.tolist()},
        returnMetadata=True,
        returnDistance=True,
    )
    hits = resp.get("vectors", [])
    if not hits:
        return {"verdict": "red", "reason": "no_hits", "query_tokens": query_tokens}

    # cosine distance -> similarity
    for h in hits:
        h["sim"] = 1.0 - h["distance"]
        h["sim_raw"] = h["sim"]  # ブースト前を保存
        h["object_id"] = h["metadata"]["object_id"]
        h["text_tokens"] = h["metadata"].get("text_tokens", [])

    # ==== 文字一致ブースト ====
    text_matched_ids = set()
    if query_tokens:
        for h in hits:
            if not h["text_tokens"]:
                continue  # 候補側に文字無し → 何もしない
            if token_match(query_tokens, h["text_tokens"], TH["text_match_ratio"]):
                h["sim"] += TH["text_boost"]
                text_matched_ids.add(h["object_id"])
            else:
                h["sim"] -= TH["text_mismatch_penalty"]  # 両方文字有りで不一致
        hits.sort(key=lambda h: h["sim"], reverse=True)

    top1 = hits[0]
    top1_id = top1["object_id"]

    # ==== マージン: top1と「別object_idの最上位」の差 ====
    #other = next((h for h in hits if h["object_id"] != top1_id), None)
    #margin = top1["sim"] - other["sim"] if other else 1.0

    other = next((h for h in hits if h["object_id"] != top1_id), None)
    margin = top1["sim_raw"] - other["sim_raw"] if other else 1.0

    # ==== 合議制: top-k内のtop1_id出現数 ====
    votes = sum(1 for h in hits if h["object_id"] == top1_id)

    # ==== 3値判定 ====
    text_hit = top1_id in text_matched_ids
    if top1["sim"] >= TH["green_sim"] and (margin >= TH["green_margin"] or text_hit):
        verdict = "green"
    elif top1["sim"] >= TH["yellow_sim"] and votes >= TH["yellow_votes"]:
        verdict = "yellow"
    else:
        verdict = "red"

    # ブースト前のランキングでのtop1(文字なしだったら誰が勝ってたか)
    raw_top1 = max(hits, key=lambda h: h["sim_raw"])

    return {
        "verdict": verdict,
        "object_id": top1_id,
        "sim": round(top1["sim"], 4),
        "sim_raw": round(top1["sim_raw"], 4),
        "raw_top1_id": raw_top1["object_id"],
        "rank_changed": raw_top1["object_id"] != top1_id,  # 文字で順位が入れ替わったか
        "margin": round(margin, 4),
        "votes": votes,
        "text_hit": text_hit,
        "query_tokens": query_tokens,
    }


def search_image_file(path):
    """画像ファイルから検索(クロップ込み)。単体テスト用エントリポイント"""
    base_img = reg.load_base_image(path)
    crop_img = reg.crop_center_object(base_img)
    return search_object(crop_img)


if __name__ == "__main__":
    import sys
    from pprint import pprint

    result = search_image_file(sys.argv[1])
    pprint(result)
