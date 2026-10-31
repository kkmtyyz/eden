"""BBox検出比較: Claude Sonnet 4 (tool use)。

Usage:
    python detect_claude.py <image_path>

出力:
    bbox_claude.jpg  — BBox描画済み画像
    crops_claude/    — 各BBoxのクロップ画像

モデル: Claude Sonnet 5 (US geo inference profile 経由)
  Nova Liteと近い価格帯。tool useで構造化出力を保証。
"""

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import boto3
from PIL import Image, ImageDraw

from eden.image_util import jpeg_bytes

PROFILE = "kkmtyyz_mfa"
MAX_SIZE = 1280
# クロスリージョン推論プロファイルを使用 (on-demand直接呼び出し不可のため)
# US geo inference profile (東京にはIn-Region無し)
#MODEL_ID = "us.anthropic.claude-sonnet-5"
#REGION = "us-east-1"
MODEL_ID = "jp.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION = "ap-northeast-1"

SYSTEM_PROMPT = "Detect all distinct physical objects in the image and report their bounding boxes using the DetectObjects tool. Coordinates are on a [0, 1000) scale."

TOOL_CONFIG = {
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


def detect_boxes_claude(img: Image.Image, client) -> list[list[int]]:
    """Claude Sonnet で tool use を使ったBBox検出。"""
    resp = client.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": jpeg_bytes(img)}}},
                    {"text": "Detect all objects in this image."},
                ],
            }
        ],
        toolConfig=TOOL_CONFIG,
    )

    # tool use レスポンスからboxes抽出
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            tool_input = block["toolUse"]["input"]
            boxes = tool_input.get("boxes", [])
            return _validate(boxes)

    # フォールバック: テキストからパース
    for block in resp["output"]["message"]["content"]:
        if "text" in block:
            return _parse_boxes(block["text"])

    return []


def _parse_boxes(text: str) -> list[list[int]]:
    """テキスト出力からBBoxをパース (フォールバック用)。"""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return _validate(parsed)
    except json.JSONDecodeError:
        pass

    # 数値グループから抽出
    groups = re.findall(r"\[\s*([\d\s,]+?)\s*\]", text)
    boxes = []
    for g in groups:
        nums = [int(n) for n in re.findall(r"\d+", g)]
        if len(nums) == 4:
            boxes.append(nums)
    return _validate(boxes)


def _validate(boxes: list, min_size: int = 5) -> list[list[int]]:
    """座標正規化・退化ボックス除去。"""
    out = []
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


def main(img_path: str):
    session = boto3.Session(profile_name=PROFILE)
    client = session.client("bedrock-runtime", region_name=REGION)

    img = Image.open(img_path).convert("RGB")
    img.thumbnail((MAX_SIZE, MAX_SIZE))

    t0 = time.time()
    boxes = detect_boxes_claude(img, client)
    elapsed = time.time() - t0

    print(f"[Claude Sonnet] detected: {len(boxes)} boxes ({elapsed:.2f}s)")

    # 描画
    out_dir = Path("crops_claude")
    out_dir.mkdir(exist_ok=True)
    vis = img.copy()
    draw = ImageDraw.Draw(vis)
    w, h = img.size

    for i, box in enumerate(boxes):
        x1, y1, x2, y2 = box
        px1, py1 = x1 / 1000 * w, y1 / 1000 * h
        px2, py2 = x2 / 1000 * w, y2 / 1000 * h
        draw.rectangle([px1, py1, px2, py2], outline="red", width=8)
        draw.text((px1, max(0, py1 - 16)), f"{i}", fill="red")

        # クロップ (6%余白)
        bw, bh = px2 - px1, py2 - py1
        pad = 0.06
        crop = img.crop((
            max(0, px1 - bw * pad),
            max(0, py1 - bh * pad),
            min(w, px2 + bw * pad),
            min(h, py2 + bh * pad),
        ))
        crop.save(out_dir / f"crop_{i}.jpg")
        print(f"  [{i}] ({x1},{y1})-({x2},{y2})")

    vis.save("bbox_claude.jpg")
    print(f"\n=> bbox_claude.jpg ({len(boxes)} boxes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python detect_claude.py <image_path>")
        sys.exit(1)
    main(sys.argv[1])
