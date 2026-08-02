"""BBox検出比較: 3モデル統合版。1枚の画像に全モデルのBBoxを重ねて出力。

Usage:
    python detect_all.py <image_path>

出力:
    bbox_all.jpg — 3モデルのBBoxを色分けして重ねた画像
        赤: Rekognition
        緑: Nova Lite
        青: Claude Sonnet 5

凡例も画像左上に描画。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import time

import boto3
from PIL import Image, ImageDraw

from eden.detection import detect_boxes
from eden.image_util import jpeg_bytes

PROFILE = "kkmtyyz_mfa"
MAX_SIZE = 1280

# Claude設定
#CLAUDE_MODEL_ID = "us.anthropic.claude-sonnet-5"
#CLAUDE_REGION = "us-east-1"
CLAUDE_MODEL_ID = "jp.anthropic.claude-sonnet-4-5-20250929-v1:0"
CLAUDE_REGION = "ap-northeast-1"


CLAUDE_SYSTEM_PROMPT = "Detect all distinct physical objects in the image and report their bounding boxes using the DetectObjects tool. Coordinates are on a [0, 1000) scale."
CLAUDE_TOOL_CONFIG = {
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

# Rekognition設定
REKOGNITION_REGION = "ap-northeast-1"
MIN_CONFIDENCE = 50

# 色定義
COLOR_REKOGNITION = (0, 180, 0)       # 緑
COLOR_NOVA = (255, 0, 0)             # 赤
COLOR_CLAUDE = (0, 80, 255)          # 青
LINE_WIDTH = 8


def detect_nova(img: Image.Image, session) -> list[list[int]]:
    """Nova Lite でBBox検出。"""
    client = session.client("bedrock-runtime", region_name="ap-northeast-1")
    t0 = time.time()
    boxes = detect_boxes(img, client=client, model_id="amazon.nova-lite-v1:0")
    elapsed = time.time() - t0
    print(f"  [Nova Lite] {len(boxes)} boxes ({elapsed:.2f}s)")
    return boxes


def detect_claude(img: Image.Image, session) -> list[list[int]]:
    """Claude Sonnet 5 でBBox検出。"""
    client = session.client("bedrock-runtime", region_name=CLAUDE_REGION)
    t0 = time.time()
    resp = client.converse(
        modelId=CLAUDE_MODEL_ID,
        system=[{"text": CLAUDE_SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": jpeg_bytes(img)}}},
                    {"text": "Detect all objects in this image."},
                ],
            }
        ],
        toolConfig=CLAUDE_TOOL_CONFIG,
    )
    elapsed = time.time() - t0

    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            boxes = block["toolUse"]["input"].get("boxes", [])
            print(f"  [Claude Sonnet 5] {len(boxes)} boxes ({elapsed:.2f}s)")
            return _validate(boxes)

    print(f"  [Claude Sonnet 5] 0 boxes (parse failed) ({elapsed:.2f}s)")
    return []


def detect_rekognition(img: Image.Image, session) -> list[list[int]]:
    """Rekognition でBBox検出。0-1000スケールに変換して返す。"""
    client = session.client("rekognition", region_name=REKOGNITION_REGION)
    t0 = time.time()
    resp = client.detect_labels(
        Image={"Bytes": jpeg_bytes(img)},
        MinConfidence=MIN_CONFIDENCE,
    )
    elapsed = time.time() - t0

    boxes = []
    for label in resp["Labels"]:
        for inst in label.get("Instances", []):
            bb = inst["BoundingBox"]
            # 0-1比率 → 0-1000スケールに変換
            x1 = int(bb["Left"] * 1000)
            y1 = int(bb["Top"] * 1000)
            x2 = int((bb["Left"] + bb["Width"]) * 1000)
            y2 = int((bb["Top"] + bb["Height"]) * 1000)
            boxes.append([x1, y1, x2, y2])

    print(f"  [Rekognition] {len(boxes)} boxes ({elapsed:.2f}s)")
    return boxes


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


def draw_boxes(draw: ImageDraw.Draw, boxes: list[list[int]], w: int, h: int, color, label: str):
    """BBox群を描画。"""
    for i, box in enumerate(boxes):
        x1, y1, x2, y2 = box
        px1, py1 = x1 / 1000 * w, y1 / 1000 * h
        px2, py2 = x2 / 1000 * w, y2 / 1000 * h
        draw.rectangle([px1, py1, px2, py2], outline=color, width=LINE_WIDTH)


def main(img_path: str):
    session = boto3.Session(profile_name=PROFILE)

    img = Image.open(img_path).convert("RGB")
    img.thumbnail((MAX_SIZE, MAX_SIZE))
    w, h = img.size

    print("Detecting...")
    boxes_rekognition = detect_rekognition(img, session)
    boxes_nova = detect_nova(img, session)
    boxes_claude = detect_claude(img, session)

    # 描画
    vis = img.copy()
    draw = ImageDraw.Draw(vis)

    # Rekognition → Nova → Claude の順に描画 (後が上に来る)
    draw_boxes(draw, boxes_rekognition, w, h, COLOR_REKOGNITION, "Rekognition")
    draw_boxes(draw, boxes_nova, w, h, COLOR_NOVA, "Nova")
    draw_boxes(draw, boxes_claude, w, h, COLOR_CLAUDE, "Claude")

    vis.save("bbox_all.jpg", quality=95)
    print(f"\n=> bbox_all.jpg")
    print(f"   Rekognition(赤): {len(boxes_rekognition)}, Nova(緑): {len(boxes_nova)}, Claude(青): {len(boxes_claude)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python detect_all.py <image_path>")
        sys.exit(1)
    main(sys.argv[1])
