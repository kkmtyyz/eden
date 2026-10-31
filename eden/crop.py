"""クロップ: BBox座標→パディング付きクロップ、中央物体選択ロジック。"""

from PIL import Image

from eden import config
from eden.detection import detect_boxes


def crop_bbox(
    img: Image.Image,
    box: list[int],
    pad: float | None = None,
) -> Image.Image:
    """BBox座標(0-1000スケール)でパディング付きクロップを行う。

    Args:
        img: 元画像
        box: [x1, y1, x2, y2] (0-1000スケール)
        pad: クロップ余白率 (Noneなら config.CROP_PAD)

    Returns:
        クロップ済み画像
    """
    pad = pad if pad is not None else config.CROP_PAD
    w, h = img.size
    x1, y1, x2, y2 = box

    # [0, 1000) → ピクセル座標
    px1, py1 = x1 / 1000 * w, y1 / 1000 * h
    px2, py2 = x2 / 1000 * w, y2 / 1000 * h

    # パディング追加
    bw, bh = px2 - px1, py2 - py1
    return img.crop((
        max(0, px1 - bw * pad),
        max(0, py1 - bh * pad),
        min(w, px2 + bw * pad),
        min(h, py2 + bh * pad),
    ))


def select_center_object(boxes: list[list[int]]) -> list[int]:
    """BBox一覧から画像中央に最も近い物体を選択する。

    距離が拮抗している場合は面積が大きい方を優先
    (中央×大 = ユーザーの狙いの物体である可能性が高い)。

    Args:
        boxes: [[x1, y1, x2, y2], ...] (0-1000スケール)

    Returns:
        選択されたBBox [x1, y1, x2, y2]
    """

    def score(b: list[int]) -> float:
        x1, y1, x2, y2 = b
        cx = (x1 + x2) / 2 / 1000
        cy = (y1 + y2) / 2 / 1000
        dist = ((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5  # 0〜0.707
        area = (x2 - x1) * (y2 - y1) / 1_000_000  # 0〜1
        return dist - area * 0.3  # 距離優先、面積で微調整。小さいほど良い

    return min(boxes, key=score)


def crop_center_object(
    img: Image.Image,
    *,
    client=None,
    pad: float | None = None,
) -> Image.Image:
    """画像中央に最も近いBBoxの物体をクロップして返す。

    BBox検出ゼロなら中央60%を切り出してフォールバック。

    Args:
        img: 入力画像 (RGB)
        client: bedrock-runtime クライアント (BBox検出用)
        pad: クロップ余白率

    Returns:
        クロップ済み画像
    """
    boxes = detect_boxes(img, client=client)
    w, h = img.size

    if not boxes:
        # フォールバック: 中央60%を切り出し
        mx, my = w * 0.2, h * 0.2
        return img.crop((mx, my, w - mx, h - my))

    best_box = select_center_object(boxes)
    return crop_bbox(img, best_box, pad=pad)
