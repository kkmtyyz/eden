"""物体登録パイプラインの各ステップを可視化するスクリプト。

Usage:
    python visualize_pipeline.py <image_path>

出力 (output/ ディレクトリに保存):
    01_original.jpg        — 元画像 (EXIF補正+リサイズ後)
    02_bbox_detected.jpg   — BBox検出結果を描画
    03_cropped.jpg         — BBoxでクロップした結果 (6%余白付き)
    04_clahe_before.jpg    — CLAHE適用前 (クロップ画像)
    04_clahe_after.jpg     — CLAHE適用後
    04_clahe_comparison.jpg — 左右比較 (before | after)
    05_rotations.jpg       — 4方向回転 (0°/90°/180°/270° 横並び)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import boto3
from PIL import Image, ImageDraw, ImageFont

from eden import config
from eden.crop import crop_bbox
from eden.detection import detect_boxes
from eden.image_util import exif_transpose, normalize_lighting

PROFILE = "kkmtyyz_mfa"
MAX_SIZE = 1024  # 本番と同じ


def load_image(path: str) -> Image.Image:
    """画像を読み込み、EXIF補正+RGB変換+リサイズ。"""
    img = Image.open(path)
    img = exif_transpose(img)
    img = img.convert("RGB")
    img.thumbnail((MAX_SIZE, MAX_SIZE))
    return img


def draw_bbox_on_image(img: Image.Image, box: list[int], color="red", width=8) -> Image.Image:
    """画像にBBoxを描画して返す。"""
    vis = img.copy()
    draw = ImageDraw.Draw(vis)
    w, h = img.size
    x1, y1, x2, y2 = box
    px1, py1 = x1 / 1000 * w, y1 / 1000 * h
    px2, py2 = x2 / 1000 * w, y2 / 1000 * h
    draw.rectangle([px1, py1, px2, py2], outline=color, width=width)
    return vis


def make_comparison(before: Image.Image, after: Image.Image, gap: int = 10) -> Image.Image:
    """2枚の画像を左右に並べる。"""
    # 高さを揃える
    h = max(before.height, after.height)
    w1, w2 = before.width, after.width

    canvas = Image.new("RGB", (w1 + gap + w2, h), (255, 255, 255))
    canvas.paste(before, (0, (h - before.height) // 2))
    canvas.paste(after, (w1 + gap, (h - after.height) // 2))
    return canvas


def make_rotation_strip(img: Image.Image, gap: int = 10) -> Image.Image:
    """4方向回転画像を横並びにする。"""
    rotations = [0, 90, 180, 270]
    images = []
    for deg in rotations:
        rot = img.rotate(-deg, expand=True) if deg != 0 else img.copy()
        images.append(rot)

    total_w = sum(im.width for im in images) + gap * (len(images) - 1)
    max_h = max(im.height for im in images)

    canvas = Image.new("RGB", (total_w, max_h), (255, 255, 255))
    x = 0
    for im in images:
        canvas.paste(im, (x, (max_h - im.height) // 2))
        x += im.width + gap

    return canvas


def main(img_path: str):
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)

    session = boto3.Session(profile_name=PROFILE)
    client = session.client("bedrock-runtime", region_name=config.BBOX_REGION)

    # ─── Step 1: 元画像読み込み ───────────────────────────────
    print("[Step 1] 元画像読み込み (EXIF補正 + リサイズ)")
    img = load_image(img_path)
    img.save(out_dir / "01_original.jpg", quality=95)
    print(f"  => {img.size}")

    # ─── Step 2: BBox検出 ─────────────────────────────────────
    print("[Step 2] BBox検出 (Nova Lite)")
    boxes = detect_boxes(img, client=client)
    print(f"  => {len(boxes)} boxes detected")

    if not boxes:
        print("  BBox検出なし。中央60%をクロップします。")
        w, h = img.size
        box = [200, 200, 800, 800]  # 中央60%相当
    else:
        # 中央×面積スコアで最良のBBoxを選択
        from eden.crop import select_center_object
        box = select_center_object(boxes)
        print(f"  => 選択BBox: {box}")

    # 全BBoxを薄く + 選択BBoxを太く描画
    vis = img.copy()
    draw = ImageDraw.Draw(vis)
    w, h = img.size
    for b in boxes:
        bx1, by1, bx2, by2 = b
        draw.rectangle(
            [bx1 / 1000 * w, by1 / 1000 * h, bx2 / 1000 * w, by2 / 1000 * h],
            outline=(200, 200, 200), width=4
        )
    # 選択BBoxを赤太線
    vis = draw_bbox_on_image(vis, box, color="red", width=8)
    vis.save(out_dir / "02_bbox_detected.jpg", quality=95)

    # ─── Step 3: クロップ ─────────────────────────────────────
    print("[Step 3] クロップ (6%余白)")
    cropped = crop_bbox(img, box)
    cropped.save(out_dir / "03_cropped.jpg", quality=95)
    print(f"  => クロップサイズ: {cropped.size}")

    # ─── Step 4: CLAHE光正規化 ────────────────────────────────
    print("[Step 4] CLAHE光正規化")
    clahe_img = normalize_lighting(cropped)
    cropped.save(out_dir / "04_clahe_before.jpg", quality=95)
    clahe_img.save(out_dir / "04_clahe_after.jpg", quality=95)

    comparison = make_comparison(cropped, clahe_img)
    comparison.save(out_dir / "04_clahe_comparison.jpg", quality=95)
    print(f"  => 比較画像サイズ: {comparison.size}")

    # ─── Step 5: 4方向回転 ────────────────────────────────────
    print("[Step 5] 4方向回転 (0°/90°/180°/270°)")
    strip = make_rotation_strip(clahe_img)
    strip.save(out_dir / "05_rotations.jpg", quality=95)
    print(f"  => 回転ストリップサイズ: {strip.size}")

    print(f"\n完了! output/ に画像を出力しました:")
    for f in sorted(out_dir.glob("*.jpg")):
        print(f"  {f.name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python visualize_pipeline.py <image_path>")
        sys.exit(1)
    main(sys.argv[1])
