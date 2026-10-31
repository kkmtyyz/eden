"""BBox検出: Nova Lite による物体バウンディングボックス検出。

Constrained Decoding (tool use) を使用して構造化出力を保証する。
"""

import json
import logging
import re

from PIL import Image

from eden import config
from eden.clients import get_bedrock_client
from eden.image_util import jpeg_bytes

logger = logging.getLogger(__name__)

BBOX_SYSTEM_PROMPT = "Detect all distinct physical objects in the image and report their bounding boxes using the DetectObjects tool. Coordinates are on a [0, 1000) scale."

# Tool use による構造化出力定義
BBOX_TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "DetectObjects",
                "description": "Report bounding boxes of all distinct physical objects detected in the image. Each box is [x1, y1, x2, y2] on a 0-1000 scale.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "boxes": {
                                "type": "array",
                                "description": "Array of bounding boxes, each [x1, y1, x2, y2] on 0-1000 scale",
                                "items": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 4,
                                    "maxItems": 4,
                                },
                            }
                        },
                        "required": ["boxes"],
                    }
                },
            }
        }
    ],
    "toolChoice": {"tool": {"name": "DetectObjects"}},
}

# フォールバック用プロンプト (tool use が失敗した場合)
BBOX_PROMPT_FALLBACK = """Detect all distinct physical objects in this image.
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

    Tool use (constrained decoding) を使用して構造化出力を保証する。
    失敗時はフォールバックとしてプロンプトベースの出力をパースする。

    Args:
        img: 入力画像 (RGB, リサイズ済み推奨)
        client: bedrock-runtime クライアント (Noneならデフォルト生成)
        model_id: 使用するモデルID (Noneなら config.BBOX_MODEL_ID)

    Returns:
        [[x1, y1, x2, y2], ...] 各座標は [0, 1000) スケール
    """
    client = client or get_bedrock_client(region=config.BBOX_REGION)
    model_id = model_id or config.BBOX_MODEL_ID

    try:
        return _detect_with_tool_use(img, client, model_id)
    except Exception as e:
        logger.warning("[detection] tool use 失敗、フォールバック: %s", e)
        return _detect_with_prompt(img, client, model_id)


def _detect_with_tool_use(
    img: Image.Image,
    client,
    model_id: str,
) -> list[list[int]]:
    """Tool use (constrained decoding) でBBox検出。"""
    resp = client.converse(
        modelId=model_id,
        system=[{"text": BBOX_SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": jpeg_bytes(img)}}},
                    {"text": "Detect all objects in this image."},
                ],
            }
        ],
        toolConfig=BBOX_TOOL_CONFIG,
        inferenceConfig={"temperature": 0.0},
    )

    # tool use レスポンスから boxes を抽出
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            tool_input = block["toolUse"]["input"]
            boxes = tool_input.get("boxes", [])
            logger.info("[detection] tool use 成功: %d boxes 検出", len(boxes))
            return _validate(boxes)

    raise ValueError("Tool use レスポンスに toolUse ブロックが見つからない")


def _detect_with_prompt(
    img: Image.Image,
    client,
    model_id: str,
) -> list[list[int]]:
    """フォールバック: プロンプトベースでBBox検出。"""
    resp = client.converse(
        modelId=model_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": jpeg_bytes(img)}}},
                    {"text": BBOX_PROMPT_FALLBACK},
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


def _validate(boxes: list, min_size: int = 5) -> list[list[int]]:
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
