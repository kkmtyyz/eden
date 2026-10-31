"""BBox検出比較: Amazon Rekognition (detect_labels)。

Usage:
    python detect_rekognition.py <image_path>

出力:
    bbox_rekognition.jpg  — BBox描画済み画像
    crops_rekognition/    — 各BBoxのクロップ画像

検出個数制限なし (MinConfidence=50 で広く拾う)。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import boto3
from PIL import Image, ImageDraw

from eden.image_util import jpeg_bytes

PROFILE = "kkmtyyz_mfa"
MAX_SIZE = 1280
REGION = "ap-northeast-1"
MIN_CONFIDENCE = 50  # 低めに設定して多く検出


def detect_boxes_rekognition(img: Image.Image, client) -> list[dict]:
    """Rekognition で物体検出。BBox付きインスタンスを返す。"""
    resp = client.detect_labels(
        Image={"Bytes": jpeg_bytes(img)},
        MinConfidence=MIN_CONFIDENCE,
    )

    dets = []
    for label in resp["Labels"]:
        for inst in label.get("Instances", []):
            bb = inst["BoundingBox"]  # Left, Top, Width, Height (0-1比率)
            dets.append({
                "label": label["Name"],
                "confidence": inst["Confidence"],
                "box": bb,
            })
    return dets


def main(img_path: str):
    session = boto3.Session(profile_name=PROFILE)
    client = session.client("rekognition", region_name=REGION)

    img = Image.open(img_path).convert("RGB")
    img.thumbnail((MAX_SIZE, MAX_SIZE))

    t0 = time.time()
    dets = detect_boxes_rekognition(img, client)
    elapsed = time.time() - t0

    print(f"[Rekognition] detected: {len(dets)} instances ({elapsed:.2f}s)")

    # 描画
    out_dir = Path("crops_rekognition")
    out_dir.mkdir(exist_ok=True)
    vis = img.copy()
    draw = ImageDraw.Draw(vis)
    w, h = img.size

    for i, d in enumerate(dets):
        bb = d["box"]
        px1 = bb["Left"] * w
        py1 = bb["Top"] * h
        px2 = (bb["Left"] + bb["Width"]) * w
        py2 = (bb["Top"] + bb["Height"]) * h

        draw.rectangle([px1, py1, px2, py2], outline="red", width=8)
        draw.text(
            (px1, max(0, py1 - 16)),
            f'{i}:{d["label"]} {d["confidence"]:.0f}%',
            fill="red",
        )

        # クロップ (6%余白)
        bw, bh = px2 - px1, py2 - py1
        pad = 0.06
        crop = img.crop((
            max(0, px1 - bw * pad),
            max(0, py1 - bh * pad),
            min(w, px2 + bw * pad),
            min(h, py2 + bh * pad),
        ))
        crop.save(out_dir / f'crop_{i}_{d["label"].replace(" ", "_")}.jpg')
        print(
            f'  [{i}] {d["label"]} ({d["confidence"]:.1f}%): '
            f"({px1:.0f},{py1:.0f})-({px2:.0f},{py2:.0f})"
        )

    vis.save("bbox_rekognition.jpg")
    print(f"\n=> bbox_rekognition.jpg ({len(dets)} instances)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python detect_rekognition.py <image_path>")
        sys.exit(1)
    main(sys.argv[1])
