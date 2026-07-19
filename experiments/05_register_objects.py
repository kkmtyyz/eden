import base64
import json
import re
import unicodedata
from io import BytesIO
from pathlib import Path

import boto3
import numpy as np
from PIL import Image, ImageOps

PROFILE = "kkmtyyz_mfa"

# Titan Multimodal EmbeddingsはTokyo(ap-northeast-1)未対応のためus-east-1固定
EMBED_REGION = "us-east-1"
EMBED_MODEL_ID = "amazon.titan-embed-image-v1"
EMBED_DIM = 1024

# テキスト抽出はNova(Tokyoで利用可)
TEXT_REGION = "ap-northeast-1"
TEXT_MODEL_ID = "amazon.nova-lite-v1:0"

# S3 Vectors(Tokyoに作成済み)
VECTOR_REGION = "ap-northeast-1"
VECTOR_BUCKET = "my-s3-vector-bucket"
VECTOR_INDEX = "my-s3-vector-index"

# BBox抽出
BBOX_REGION = "ap-northeast-1"
BBOX_MODEL_ID = "amazon.nova-lite-v1:0"

MAX_IMAGE_SIZE = 1024
ROTATIONS = [0, 90, 180, 270]

session = boto3.Session(profile_name=PROFILE)
embed_client = session.client("bedrock-runtime", region_name=EMBED_REGION)
text_client = session.client("bedrock-runtime", region_name=TEXT_REGION)
bbox_client = session.client("bedrock-runtime", region_name=BBOX_REGION)
s3v_client = session.client("s3vectors", region_name=VECTOR_REGION)

TEXT_PROMPT = """This image shows a physical object.
If there is any text, logo, or printed/engraved characters visible on the object, transcribe them exactly as written.
If there is no visible text, output exactly: NONE
Output ONLY the transcribed text (or NONE), no explanation, no extra formatting."""

BBOX_PROMPT = """Detect all distinct physical objects in this image.
For each object, output its bounding box on a scale of [0, 1000).
Output ONLY a LIST, no other text:
[ [x1, y1, x2, y2], ...]"""
CROP_PAD = 0.12  # 既存プログラムと同じ余白


def repair_boxes(text, min_size=5):
    """
    LLM出力から [[x1,y1,x2,y2], ...] を復元する保険関数。
    括弧の欠け・過剰・前置きテキスト・フラット出力に耐える。
    復元不能なグループは黙って捨てる(全滅なら[]を返す)。
    """
    text = text.strip()
    # コードフェンス除去
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()

    # --- 1. まず素直にパース ---
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            # [[...]] 形式ならそのまま、フラット[1,2,3,4]なら1箱として包む
            if parsed and all(isinstance(v, (int, float)) for v in parsed):
                parsed = [parsed]
            return _validate(parsed, min_size)
    except json.JSONDecodeError:
        pass

    # --- 2. 内側括弧グループから復元 ---
    # "[53, 408, 150, 500]" や "[53,408,150,500" (閉じ欠け) っぽい塊を拾う
    groups = re.findall(r"\[\s*([\d\s,.\-]+?)\s*(?:\]|$|\[)", text)
    boxes = []
    for g in groups:
        nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", g)]
        if len(nums) == 4:  # 4個ちょうどの塊だけ採用
            boxes.append(nums)
        # len 8, 12... は「区切りの ], [ が丸ごと欠けた」ケースなので分割して救済
        elif len(nums) > 4 and len(nums) % 4 == 0:
            boxes.extend([nums[i : i + 4] for i in range(0, len(nums), 4)])
        # 3個や5個は復元不能とみなして捨てる

    # --- 3. 括弧グループが取れなかったらフラット数列として救済 ---
    if not boxes:
        nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
        if nums and len(nums) % 4 == 0:
            boxes = [nums[i : i + 4] for i in range(0, len(nums), 4)]

    return _validate(boxes, min_size)


def _validate(boxes, min_size):
    """クランプ・座標順の正規化・退化ボックスの除去"""
    out = []
    for b in boxes:
        try:
            x1, y1, x2, y2 = (max(0, min(1000, int(v))) for v in b)
        except (ValueError, TypeError):
            continue
        if x1 > x2:
            x1, x2 = x2, x1  # 順序逆転は入れ替えて救済
        if y1 > y2:
            y1, y2 = y2, y1
        if (x2 - x1) < min_size or (y2 - y1) < min_size:
            continue  # 点みたいな退化ボックスは捨てる
        out.append([x1, y1, x2, y2])
    return out


def detect_boxes(img):
    resp = bbox_client.converse(
        modelId=BBOX_MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": jpeg_bytes(img)}}},
                    {"text": BBOX_PROMPT},
                ],
            }
        ],
        inferenceConfig={"temperature": 0.0, "maxTokens": 2000},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    return repair_boxes(text)


def crop_center_object(img):
    """画像中央に最も近いBBoxの物体をクロップして返す。
    検出ゼロなら中央60%を切り出してフォールバック"""
    boxes = detect_boxes(img)
    w, h = img.size

    if not boxes:
        print("  !! BBox検出ゼロ、中央60%でフォールバック")
        mx, my = w * 0.2, h * 0.2
        return img.crop((mx, my, w - mx, h - my))

    # 画像中心とBBox中心の距離が最小のものを選ぶ
    # 距離が拮抗してたら面積が大きい方を優先(中央×大 = ユーザーの狙いの物体である可能性が高い)
    def score(b):
        x1, y1, x2, y2 = b
        cx = (x1 + x2) / 2 / 1000
        cy = (y1 + y2) / 2 / 1000
        dist = ((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5  # 0〜0.707
        area = (x2 - x1) * (y2 - y1) / 1_000_000  # 0〜1
        return dist - area * 0.3  # 距離優先、面積で微調整。小さいほど良い

    b = min(boxes, key=score)
    x1, y1, x2, y2 = b
    px1, py1 = x1 / 1000 * w, y1 / 1000 * h
    px2, py2 = x2 / 1000 * w, y2 / 1000 * h
    bw, bh = px2 - px1, py2 - py1
    return img.crop(
        (
            max(0, px1 - bw * CROP_PAD),
            max(0, py1 - bh * CROP_PAD),
            min(w, px2 + bw * CROP_PAD),
            min(h, py2 + bh * CROP_PAD),
        )
    )


def ensure_index():
    """my-s3-vector-index が無ければ作成する(あれば何もしない)"""
    try:
        s3v_client.create_index(
            vectorBucketName=VECTOR_BUCKET,
            indexName=VECTOR_INDEX,
            dataType="float32",
            dimension=EMBED_DIM,
            distanceMetric="cosine",
            metadataConfiguration={
                "nonFilterableMetadataKeys": ["label", "source_file"]
            },
        )
        print(f"=> index作成: {VECTOR_INDEX} (dim={EMBED_DIM})")
    except s3v_client.exceptions.ConflictException:
        print(f"=> index既存: {VECTOR_INDEX} (再利用)")


def load_base_image(path):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # EXIF向き補正
    img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE))
    return img


def jpeg_bytes(img):
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def extract_text(img):
    """VLMで物体表面の文字を抽出(base画像1枚のみ。回転前でOK、文字は回転不変の情報だから)"""
    resp = text_client.converse(
        modelId=TEXT_MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": jpeg_bytes(img)}}},
                    {"text": TEXT_PROMPT},
                ],
            }
        ],
        inferenceConfig={"temperature": 0.0, "maxTokens": 200},
    )
    text = resp["output"]["message"]["content"][0]["text"].strip()
    if text.upper() == "NONE":
        return ""
    return text


def normalize_text(text):
    """全角半角統一・小文字化・記号除去してトークン化"""
    if not text:
        return []
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    # 英数字・ひらがな・カタカナ・漢字以外は区切りとして除去
    text = re.sub(r"[^\w\u3040-\u30ff\u30a0-\u30ff\u4e00-\u9fff]+", " ", text)
    tokens = sorted(set(t for t in text.split() if t))
    return tokens


def embed_image(img):
    b64 = base64.b64encode(jpeg_bytes(img)).decode()
    resp = embed_client.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=json.dumps(
            {
                "inputImage": b64,
                "embeddingConfig": {"outputEmbeddingLength": EMBED_DIM},
            }
        ),
    )
    return np.array(json.loads(resp["body"].read())["embedding"], dtype=np.float32)


def register_object(obj_dir):
    object_id = obj_dir.name
    vectors = []

    files = sorted(obj_dir.glob("*.jpg")) + sorted(obj_dir.glob("*.png"))
    for f in files:
        print(f"processing: {f}")
        base_img = load_base_image(f)

        # 中央物体をクロップ(検索時と同じ形に揃える)
        crop_img = crop_center_object(base_img)
        # 目視確認用に保存しておく(PoCでは必ずチェックすること)
        debug_dir = Path("debug_crops") / object_id
        debug_dir.mkdir(parents=True, exist_ok=True)
        crop_img.save(debug_dir / f"{f.stem}.jpg")

        raw_text = extract_text(crop_img)  # 文字抽出もクロップ後の画像に対して実施
        tokens = normalize_text(raw_text)
        if tokens:
            print(f"  text_tokens: {tokens}")

        for deg in ROTATIONS:
            rot_img = crop_img.rotate(-deg, expand=True)
            vec = embed_image(rot_img)

            # 空配列はS3 Vectorsで許されないため、text_tokensは有る時だけ入れる
            metadata = {
                "object_id": object_id,
                "rotation": deg,
                "label": object_id,
                "source_file": str(f),
            }
            if tokens:
                metadata["text_tokens"] = tokens

            vectors.append(
                {
                    "key": f"{object_id}__{f.stem}__{deg}",
                    "data": {"float32": vec.tolist()},
                    "metadata": metadata,
                }
            )

    return vectors


def put_vectors_batched(vectors, batch_size=500):
    for i in range(0, len(vectors), batch_size):
        batch = vectors[i : i + batch_size]
        s3v_client.put_vectors(
            vectorBucketName=VECTOR_BUCKET,
            indexName=VECTOR_INDEX,
            vectors=batch,
        )
        print(f"  put_vectors: {i + len(batch)}/{len(vectors)}")


def main():
    ensure_index()

    all_vectors = []
    for obj_dir in sorted(Path("dataset").iterdir()):
        if not obj_dir.is_dir():
            continue
        print(f"\n=== object: {obj_dir.name} ===")
        vecs = register_object(obj_dir)
        all_vectors.extend(vecs)

    print(f"\n=> 登録対象ベクトル数: {len(all_vectors)}")
    put_vectors_batched(all_vectors)
    print("=> S3 Vectors登録完了")


if __name__ == "__main__":
    main()
