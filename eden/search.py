"""ベクトル検索: S3 Vectors query + スコア計算 + 3値判定。"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from PIL import Image

from eden import config
from eden.clients import get_bedrock_embed_client, get_s3vectors_client
from eden.crop import crop_center_object
from eden.embedding import embed_image
from eden.ocr import extract_text, normalize_text


@dataclass
class SearchResult:
    """検索結果を格納するデータクラス。"""

    verdict: str  # "green" | "yellow" | "red"
    object_id: str | None = None
    sim: float = 0.0  # ブースト後のtop1類似度
    sim_raw: float = 0.0  # ブースト前のtop1類似度
    raw_top1_id: str | None = None  # ブースト前のtop1 object_id
    rank_changed: bool = False  # 文字ブーストで順位が入れ替わったか
    margin: float = 0.0  # top1とtop2(別object_id)のsim_raw差
    votes: int = 0  # top-k内のtop1_id出現数
    text_hit: bool = False  # 文字一致があったか
    query_tokens: list[str] = field(default_factory=list)
    reason: str | None = None  # 判定補足情報


def token_match(
    query_tokens: list[str],
    cand_tokens: list[str],
    ratio_th: float | None = None,
) -> bool:
    """部分一致 or 編集距離でトークン同士の一致を判定する。

    Args:
        query_tokens: クエリ側トークン
        cand_tokens: 候補側トークン
        ratio_th: SequenceMatcher一致率の閾値 (Noneなら config.TEXT_MATCH_RATIO)

    Returns:
        いずれかのトークンペアが一致すればTrue
    """
    ratio_th = ratio_th if ratio_th is not None else config.TEXT_MATCH_RATIO
    for q in query_tokens:
        for c in cand_tokens:
            if q in c or c in q:
                return True
            if SequenceMatcher(None, q, c).ratio() >= ratio_th:
                return True
    return False


def query_vectors(
    vector: list[float],
    *,
    top_k: int | None = None,
    client=None,
    bucket: str | None = None,
    index: str | None = None,
) -> list[dict]:
    """S3 Vectorsにクエリを投げてヒットリストを返す。

    Args:
        vector: クエリベクトル (float32のリスト)
        top_k: 返却件数 (Noneなら config.TOP_K)
        client: s3vectors クライアント
        bucket: ベクトルバケット名
        index: インデックス名

    Returns:
        ヒットリスト (distance, metadata付き)
    """
    client = client or get_s3vectors_client()
    top_k = top_k or config.TOP_K
    bucket = bucket or config.VECTOR_BUCKET
    index = index or config.VECTOR_INDEX

    resp = client.query_vectors(
        vectorBucketName=bucket,
        indexName=index,
        topK=top_k,
        queryVector={"float32": vector},
        returnMetadata=True,
        returnDistance=True,
    )
    return resp.get("vectors", [])


def compute_scores(
    hits: list[dict],
    query_tokens: list[str],
) -> tuple[list[dict], set[str]]:
    """ヒットリストにsim計算・文字一致ブースト/ペナルティを適用する。

    Args:
        hits: query_vectorsの戻り値
        query_tokens: クエリ画像から抽出したテキストトークン

    Returns:
        (スコア付きhitsリスト, 文字一致したobject_idのセット)
    """
    # cosine distance → similarity
    for h in hits:
        h["sim"] = 1.0 - h["distance"]
        h["sim_raw"] = h["sim"]
        h["object_id"] = h["metadata"]["object_id"]
        h["text_tokens"] = h["metadata"].get("text_tokens", [])

    # 文字一致ブースト
    text_matched_ids: set[str] = set()
    if query_tokens:
        for h in hits:
            if not h["text_tokens"]:
                continue  # 候補側に文字無し → 何もしない
            if token_match(query_tokens, h["text_tokens"]):
                h["sim"] += config.TEXT_BOOST
                text_matched_ids.add(h["object_id"])
            else:
                # 両方文字有りで不一致 → ペナルティ
                h["sim"] -= config.TEXT_MISMATCH_PENALTY
        hits.sort(key=lambda h: h["sim"], reverse=True)

    return hits, text_matched_ids


def judge_verdict(
    hits: list[dict],
    text_matched_ids: set[str],
) -> dict:
    """スコア付きヒットリストから3値判定を行う。

    Args:
        hits: compute_scoresで加工済みのヒットリスト
        text_matched_ids: 文字一致したobject_idのセット

    Returns:
        判定結果の辞書
    """
    top1 = hits[0]
    top1_id = top1["object_id"]

    # マージン: top1と「別object_idの最上位」のsim_raw差
    other = next((h for h in hits if h["object_id"] != top1_id), None)
    margin = top1["sim_raw"] - other["sim_raw"] if other else 1.0

    # 合議制: top-k内のtop1_id出現数
    votes = sum(1 for h in hits if h["object_id"] == top1_id)

    # 3値判定
    text_hit = top1_id in text_matched_ids
    if top1["sim"] >= config.GREEN_SIM and (margin >= config.GREEN_MARGIN or text_hit):
        verdict = "green"
    elif top1["sim"] >= config.YELLOW_SIM and votes >= 2:
        verdict = "yellow"
    else:
        verdict = "red"

    # ブースト前ランキングでのtop1
    raw_top1 = max(hits, key=lambda h: h["sim_raw"])

    return {
        "verdict": verdict,
        "object_id": top1_id,
        "sim": round(top1["sim"], 4),
        "sim_raw": round(top1["sim_raw"], 4),
        "raw_top1_id": raw_top1["object_id"],
        "rank_changed": raw_top1["object_id"] != top1_id,
        "margin": round(margin, 4),
        "votes": votes,
        "text_hit": text_hit,
    }


def search_object(
    img: Image.Image,
    *,
    embed_client=None,
    s3v_client=None,
    text_client=None,
) -> SearchResult:
    """クロップ済み画像1枚を検索して判定結果を返す。

    Args:
        img: クロップ済み画像
        embed_client: bedrock-runtime クライアント (Embeddings用)
        s3v_client: s3vectors クライアント
        text_client: bedrock-runtime クライアント (テキスト抽出用)

    Returns:
        SearchResult
    """
    # 文字抽出(クエリ側は1回だけ、回転なし)
    raw_text = extract_text(img, client=text_client)
    query_tokens = normalize_text(raw_text)

    # 埋め込み
    vec = embed_image(img, client=embed_client)

    # ベクトル検索
    hits = query_vectors(vec.tolist(), client=s3v_client)

    if not hits:
        return SearchResult(
            verdict="red",
            reason="no_hits",
            query_tokens=query_tokens,
        )

    # スコア計算 + 文字ブースト
    hits, text_matched_ids = compute_scores(hits, query_tokens)

    # 3値判定
    result = judge_verdict(hits, text_matched_ids)

    return SearchResult(
        verdict=result["verdict"],
        object_id=result["object_id"],
        sim=result["sim"],
        sim_raw=result["sim_raw"],
        raw_top1_id=result["raw_top1_id"],
        rank_changed=result["rank_changed"],
        margin=result["margin"],
        votes=result["votes"],
        text_hit=result["text_hit"],
        query_tokens=query_tokens,
    )


def search_image(
    img: Image.Image,
    *,
    bbox_client=None,
    embed_client=None,
    s3v_client=None,
    text_client=None,
) -> SearchResult:
    """元画像から検索する(クロップ込み)。

    BBox検出→中央物体クロップ→search_object の一連の流れ。

    Args:
        img: 元画像 (RGB, リサイズ済み推奨)
        bbox_client: bedrock-runtime クライアント (BBox検出用)
        embed_client: bedrock-runtime クライアント (Embeddings用)
        s3v_client: s3vectors クライアント
        text_client: bedrock-runtime クライアント (テキスト抽出用)

    Returns:
        SearchResult
    """
    crop_img = crop_center_object(img, client=bbox_client)
    return search_object(
        crop_img,
        embed_client=embed_client,
        s3v_client=s3v_client,
        text_client=text_client,
    )
