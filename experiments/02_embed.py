"""画像埋め込みのバッチ生成スクリプト。eden.embedding を利用。"""

from pathlib import Path

import boto3
import numpy as np

from eden.clients import get_bedrock_embed_client
from eden.embedding import embed_image
from eden.image_util import load_image

PROFILE = "kkmtyyz_mfa"

session = boto3.Session(profile_name=PROFILE)
client = get_bedrock_embed_client(session=session)


def main():
    vecs, labels, files = [], [], []
    for obj_dir in sorted(Path("dataset").iterdir()):
        if not obj_dir.is_dir():
            continue
        for f in sorted(obj_dir.glob("*.jpg")) + sorted(obj_dir.glob("*.png")):
            print(f"embedding: {f}")
            img = load_image(str(f))
            vecs.append(embed_image(img, client=client))
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
