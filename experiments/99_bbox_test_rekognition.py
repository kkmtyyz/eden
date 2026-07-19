"""BBox検出の Rekognition 版比較実験。eden.clients を利用。"""

import sys
from pathlib import Path

import boto3
from PIL import Image, ImageDraw

from eden.clients import get_rekognition_client
from eden.image_util import jpeg_bytes

PROFILE = "kkmtyyz_mfa"
MAX_SIZE = 1280  # Nova版と条件を揃える

session = boto3.Session(profile_name=PROFILE)
client = get_rekognition_client(session=session)

MIN_CONFIDENCE = 50  # 検出感度。比較目的なら下げてもいい
PAD = 0.12  # Nova版と同じ余白


def load_resized(path):
    img = Image.open(path).convert("RGB")
    img.thumbnail((MAX_SIZE, MAX_SIZE))
    return img


def detect(img_path):
    img = load_resized(img_path)

    resp = client.detect_labels(
        Image={"Bytes": jpeg_bytes(img)},
        MinConfidence=MIN_CONFIDENCE,
    )

    # Instances(=bbox付き)を持つラベルだけ集める
    dets = []
    for label in resp["Labels"]:
        for inst in label.get("Instances", []):
            bb = inst["BoundingBox"]  # Left, Top, Width, Height (0-1の比率)
            dets.append(
                {
                    "label": label["Name"],
                    "confidence": inst["Confidence"],
                    "box": bb,
                }
            )
    return img, dets


def main(img_path):
    img, dets = detect(img_path)
    w, h = img.size
    out_dir = Path("crops_rekognition")
    out_dir.mkdir(exist_ok=True)
    vis = img.copy()
    draw = ImageDraw.Draw(vis)

    print(f"detected instances: {len(dets)}")

    for i, d in enumerate(dets):
        bb = d["box"]
        # 比率 -> pixel
        px1 = bb["Left"] * w
        py1 = bb["Top"] * h
        px2 = (bb["Left"] + bb["Width"]) * w
        py2 = (bb["Top"] + bb["Height"]) * h

        draw.rectangle([px1, py1, px2, py2], outline="red", width=3)
        draw.text(
            (px1, max(0, py1 - 14)),
            f'{i}:{d["label"]} {d["confidence"]:.0f}%',
            fill="red",
        )

        # padded crop
        bw, bh = px2 - px1, py2 - py1
        crop = img.crop(
            (
                max(0, px1 - bw * PAD),
                max(0, py1 - bh * PAD),
                min(w, px2 + bw * PAD),
                min(h, py2 + bh * PAD),
            )
        )
        crop.save(out_dir / f'crop_{i}_{d["label"].replace(" ", "_")}.jpg')
        print(
            f'[{i}] {d["label"]} ({d["confidence"]:.1f}%): '
            f"({px1:.0f},{py1:.0f})-({px2:.0f},{py2:.0f})"
        )

    vis.save("bbox_result_rekognition.jpg")
    print("\n=> bbox_result_rekognition.jpg と crops_rekognition/ を目視チェック")


if __name__ == "__main__":
    main(sys.argv[1])
