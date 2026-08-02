"""BBox検出比較: Amazon Nova Lite (tool use)。

Usage:
    python detect_nova.py <image_path>

出力:
    bbox_nova.jpg  — BBox描画済み画像
    crops_nova/    — 各BBoxのクロップ画像
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import time

import boto3
from PIL import Image, ImageDraw, ImageFont

from eden.detection import detect_boxes
from eden.image_util import jpeg_bytes

PROFILE = "kkmtyyz_mfa"
MAX_SIZE = 1280


def main(img_path: str):
    session = boto3.Session(profile_name=PROFILE)
    client = session.client("bedrock-runtime", region_name="ap-northeast-1")

    img = Image.open(img_path).convert("RGB")
    img.thumbnail((MAX_SIZE, MAX_SIZE))

    t0 = time.time()
    boxes = detect_boxes(img, client=client, model_id="amazon.nova-lite-v1:0")
    elapsed = time.time() - t0

    print(f"[Nova Lite] detected: {len(boxes)} boxes ({elapsed:.2f}s)")

    # 描画
    out_dir = Path("crops_nova")
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

    vis.save("bbox_nova.jpg")
    print(f"\n=> bbox_nova.jpg ({len(boxes)} boxes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python detect_nova.py <image_path>")
        sys.exit(1)
    main(sys.argv[1])
