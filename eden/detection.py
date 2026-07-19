"""BBox検出: Nova Lite による物体バウンディングボックス検出。"""

import json
import re

from PIL import Image

from eden import config
from eden.clients import get_bedrock_client
from eden.image_util import jpeg_bytes

BBOX_PROMPT = """Detect all distinct physical objects in this image.
For each object, output its bounding box on a scale of [0, 1000).
Output ONLY a LIST, no other text:
[ [x1, y1, x2, y2], ...]"""


def detect_boxes(
    img: Image.Image,
    *,
    client=None,
    model_id: str | None = None,
) -> list[list[int]]:
    """画像から物体のBBox一覧を検出して返す。

    Args:
        img: 入力画像 (RGB, リサイズ済み推奨)
        client: bedrock-runtime クライアント (Noneならデフォルト生成)
        model_id: 使用するモデルID (Noneなら config.BBOX_MODEL_ID)

    Returns:
        [[x1, y1, x2, y2], ...] 各座標は [0, 1000) スケール
    """
    client = client or get_bedrock_client(region=config.BBOX_REGION)
    model_id = model_id or config.BBOX_MODEL_ID

    resp = client.converse(
        modelId=model_id,
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


def repair_boxes(text: str, min_size: int = 5) -> list[list[int]]:
    """LLM出力から [[x1,y1,x2,y2], ...] を復元する保険関数。

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
    groups = re.findall(r"\[\s*([\d\s,.\-]+?)\s*(?:\]|$|\[)", text)
    boxes: list[list[int]] = []
    for g in groups:
        nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", g)]
        if len(nums) == 4:
            boxes.append(nums)
        elif len(nums) > 4 and len(nums) % 4 == 0:
            boxes.extend([nums[i : i + 4] for i in range(0, len(nums), 4)])

    # --- 3. 括弧グループが取れなかったらフラット数列として救済 ---
    if not boxes:
        nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
        if nums and len(nums) % 4 == 0:
            boxes = [nums[i : i + 4] for i in range(0, len(nums), 4)]

    return _validate(boxes, min_size)


def _validate(boxes: list, min_size: int) -> list[list[int]]:
    """クランプ・座標順の正規化・退化ボックスの除去。"""
    out: list[list[int]] = []
    for b in boxes:
        try:
            x1, y1, x2, y2 = (max(0, min(1000, int(v))) for v in b)
        except (ValueError, TypeError):
            continue
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1
        if (x2 - x1) < min_size or (y2 - y1) < min_size:
            continue
        out.append([x1, y1, x2, y2])
    return out
