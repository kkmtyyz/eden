import sys
import numpy as np
from pathlib import Path
from PIL import Image
from io import BytesIO
import json
import boto3

REGION = "us-east-1"
MODEL_ID = "amazon.titan-embed-image-v1"
session = boto3.Session(profile_name="kkmtyyz_mfa")
client = session.client("bedrock-runtime", region_name=REGION)


def embed_image(path):
    img = Image.open(path).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    import base64

    b64 = base64.b64encode(buf.getvalue()).decode()
    resp = client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(
            {"inputImage": b64, "embeddingConfig": {"outputEmbeddingLength": 1024}}
        ),
    )
    v = np.array(json.loads(resp["body"].read())["embedding"], dtype=np.float32)
    return v / np.linalg.norm(v)


def main(unknown_dir):
    d = np.load("embeddings.npz")
    vecs, labels = d["vecs"], d["labels"]
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    print(f"{'file':40s} {'nearest_label':20s} {'distance':>8s}  judge(θ=0.20)")
    for f in sorted(Path(unknown_dir).glob("*.jpg")):
        qv = embed_image(f)
        dist = 1.0 - vecs @ qv
        idx = dist.argmin()
        d_min = dist[idx]
        judge = "MATCH!!" if d_min <= 0.20 else "reject(正しい)"
        print(f"{f.name:40s} {labels[idx]:20s} {d_min:8.4f}  {judge}")


if __name__ == "__main__":
    main(sys.argv[1])
