"""BBox検出のテストスクリプト。eden.detection を利用。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from PIL import Image, ImageDraw

from eden import config
from eden.clients import get_bedrock_client
from eden.crop import crop_bbox
from eden.detection import detect_boxes
from eden.image_util import load_image

PROFILE = "kkmtyyz_mfa"
MAX_SIZE = 1280  # 送信前に縮小(コスト・レイテンシ対策)

session = boto3.Session(profile_name=PROFILE)
client = session.client("bedrock-runtime", region_name=config.BBOX_REGION)


def detect(img_path):
    img = Image.open(img_path).convert("RGB")
    img.thumbnail((MAX_SIZE, MAX_SIZE))
    boxes = detect_boxes(img, client=client)
    print(boxes)
    return img, boxes


def main(img_path):
    img, dets = detect(img_path)
    w, h = img.size
    out_dir = Path("crops")
    out_dir.mkdir(exist_ok=True)
    vis = img.copy()
    draw = ImageDraw.Draw(vis)

    for i, d in enumerate(dets):
        print(d)
        x1, y1, x2, y2 = d
        # [0,1000) -> pixel
        px1, py1 = x1 / 1000 * w, y1 / 1000 * h
        px2, py2 = x2 / 1000 * w, y2 / 1000 * h
        draw.rectangle([px1, py1, px2, py2], outline="red", width=3)
        # padded crop
        crop = crop_bbox(img, d)
        crop.save(out_dir / f"crop_{i}.jpg")

    vis.save("bbox_result.jpg")
    print("\n=> bbox_result.jpg と crops/ を目視チェック")


if __name__ == "__main__":
    main(sys.argv[1])
