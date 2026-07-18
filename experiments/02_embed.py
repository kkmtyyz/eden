import boto3
import json
import base64
from pathlib import Path
from io import BytesIO
import numpy as np
from PIL import Image

REGION = "us-east-1"
MODEL_ID = "amazon.titan-embed-image-v1"
session = boto3.Session(profile_name="kkmtyyz_mfa")
client = session.client("bedrock-runtime", region_name=REGION)


def embed_image(path):
    img = Image.open(path).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()
    resp = client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(
            {
                "inputImage": b64,
                "embeddingConfig": {"outputEmbeddingLength": 1024},
            }
        ),
    )
    return np.array(json.loads(resp["body"].read())["embedding"], dtype=np.float32)


def main():
    vecs, labels, files = [], [], []
    for obj_dir in sorted(Path("dataset").iterdir()):
        if not obj_dir.is_dir():
            continue
        for f in sorted(obj_dir.glob("*.jpg")) + sorted(obj_dir.glob("*.png")):
            print(f"embedding: {f}")
            vecs.append(embed_image(f))
            labels.append(obj_dir.name)
            files.append(str(f))
    np.savez(
        "embeddings.npz",
        vecs=np.stack(vecs),
        labels=np.array(labels),
        files=np.array(files),
    )
    print(f"\n=> embeddings.npz 保存 ({len(vecs)}枚)")


if __name__ == "__main__":
    main()
