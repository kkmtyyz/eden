"""未登録物体のテストスクリプト。eden.embedding を利用。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
import numpy as np

from eden.clients import get_bedrock_embed_client
from eden.embedding import embed_image
from eden.image_util import load_image

PROFILE = "kkmtyyz_mfa"

session = boto3.Session(profile_name=PROFILE)
client = get_bedrock_embed_client(session=session)


def main(unknown_dir):
    d = np.load("embeddings.npz")
    vecs, labels = d["vecs"], d["labels"]
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    print(f"{'file':40s} {'nearest_label':20s} {'distance':>8s}  judge(θ=0.20)")
    for f in sorted(Path(unknown_dir).glob("*.jpg")):
        img = load_image(str(f))
        qv = embed_image(img, client=client)
        qv = qv / np.linalg.norm(qv)
        dist = 1.0 - vecs @ qv
        idx = dist.argmin()
        d_min = dist[idx]
        judge = "MATCH!!" if d_min <= 0.20 else "reject(正しい)"
        print(f"{f.name:40s} {labels[idx]:20s} {d_min:8.4f}  {judge}")


if __name__ == "__main__":
    main(sys.argv[1])
