"""画像埋め込み: Amazon Titan Multimodal Embeddings (4方向回転対応)。"""

import base64
import json

import numpy as np
from PIL import Image

from eden import config
from eden.clients import get_bedrock_embed_client
from eden.image_util import jpeg_bytes


def embed_image(
    img: Image.Image,
    *,
    client=None,
    model_id: str | None = None,
    dim: int | None = None,
) -> np.ndarray:
    """画像を埋め込みベクトルに変換する。

    Args:
        img: 入力画像 (RGB)
        client: bedrock-runtime クライアント (Noneならデフォルト生成)
        model_id: 使用するモデルID (Noneなら config.EMBED_MODEL_ID)
        dim: 出力次元数 (Noneなら config.EMBED_DIM)

    Returns:
        float32のnumpy配列 (shape: (dim,))
    """
    client = client or get_bedrock_embed_client()
    model_id = model_id or config.EMBED_MODEL_ID
    dim = dim or config.EMBED_DIM

    b64 = base64.b64encode(jpeg_bytes(img)).decode()
    resp = client.invoke_model(
        modelId=model_id,
        body=json.dumps({
            "inputImage": b64,
            "embeddingConfig": {"outputEmbeddingLength": dim},
        }),
    )
    embedding = json.loads(resp["body"].read())["embedding"]
    return np.array(embedding, dtype=np.float32)


def embed_rotations(
    img: Image.Image,
    *,
    rotations: list[int] | None = None,
    client=None,
    model_id: str | None = None,
    dim: int | None = None,
) -> list[tuple[int, np.ndarray]]:
    """画像を複数角度で回転し、それぞれの埋め込みベクトルを返す。

    Args:
        img: 入力画像 (RGB, クロップ済み推奨)
        rotations: 回転角度リスト (Noneなら config.ROTATIONS = [0, 90, 180, 270])
        client: bedrock-runtime クライアント
        model_id: 使用するモデルID
        dim: 出力次元数

    Returns:
        [(angle, vector), ...] のリスト
    """
    rotations = rotations if rotations is not None else config.ROTATIONS
    client = client or get_bedrock_embed_client()

    results: list[tuple[int, np.ndarray]] = []
    for deg in rotations:
        rot_img = img.rotate(-deg, expand=True) if deg != 0 else img
        vec = embed_image(rot_img, client=client, model_id=model_id, dim=dim)
        results.append((deg, vec))

    return results
