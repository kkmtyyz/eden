"""物体登録スクリプト。eden パッケージを利用。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

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

PROFILE = "kkmtyyz_mfa"

session = boto3.Session(profile_name=PROFILE)
bbox_client = get_bedrock_client(session=session)
embed_client = get_bedrock_embed_client(session=session)
text_client = get_bedrock_text_client(session=session)
s3v_client = get_s3vectors_client(session=session)


def ensure_index():
    """my-s3-vector-index が無ければ作成する(あれば何もしない)"""
    try:
        s3v_client.create_index(
            vectorBucketName=config.VECTOR_BUCKET,
            indexName=config.VECTOR_INDEX,
            dataType="float32",
            dimension=config.EMBED_DIM,
            distanceMetric="cosine",
            metadataConfiguration={
                "nonFilterableMetadataKeys": ["label", "source_file"]
            },
        )
        print(f"=> index作成: {config.VECTOR_INDEX} (dim={config.EMBED_DIM})")
    except s3v_client.exceptions.ConflictException:
        print(f"=> index既存: {config.VECTOR_INDEX} (再利用)")


def register_object(obj_dir):
    object_id = obj_dir.name
    vectors = []

    files = sorted(obj_dir.glob("*.jpg")) + sorted(obj_dir.glob("*.png"))
    for f in files:
        print(f"processing: {f}")
        base_img = load_image(str(f))

        # 中央物体をクロップ(検索時と同じ形に揃える)
        crop_img = crop_center_object(base_img, client=bbox_client)
        # 目視確認用に保存しておく(PoCでは必ずチェックすること)
        debug_dir = Path("debug_crops") / object_id
        debug_dir.mkdir(parents=True, exist_ok=True)
        crop_img.save(debug_dir / f"{f.stem}.jpg")

        raw_text = extract_text(crop_img, client=text_client)
        tokens = normalize_text(raw_text)
        if tokens:
            print(f"  text_tokens: {tokens}")

        for deg in config.ROTATIONS:
            rot_img = crop_img.rotate(-deg, expand=True) if deg != 0 else crop_img
            vec = embed_image(rot_img, client=embed_client)

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
            vectorBucketName=config.VECTOR_BUCKET,
            indexName=config.VECTOR_INDEX,
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
