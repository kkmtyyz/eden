"""画像ユーティリティ: JPEG変換、EXIF補正、リサイズ、光正規化等。"""

from io import BytesIO

import numpy as np
from PIL import Image, ImageOps

from eden import config


def load_image(path: str) -> Image.Image:
    """画像ファイルを読み込み、EXIF回転補正→RGB変換→リサイズして返す。"""
    img = Image.open(path)
    img = exif_transpose(img)
    img = img.convert("RGB")
    img.thumbnail((config.MAX_IMAGE_SIZE, config.MAX_IMAGE_SIZE))
    return img


def exif_transpose(img: Image.Image) -> Image.Image:
    """EXIF Orientation タグに基づいて画像を正しい向きに回転する。"""
    return ImageOps.exif_transpose(img)


def jpeg_bytes(img: Image.Image, quality: int = 90) -> bytes:
    """PIL ImageをJPEGバイト列に変換する。"""
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def resize(img: Image.Image, max_size: int | None = None) -> Image.Image:
    """アスペクト比を保ちつつ max_size 以下にリサイズする。

    max_size未指定時は config.MAX_IMAGE_SIZE を使用。
    既にサイズ以下の場合はコピーを返す。
    """
    max_size = max_size or config.MAX_IMAGE_SIZE
    img = img.copy()
    img.thumbnail((max_size, max_size))
    return img


def normalize_lighting(img: Image.Image) -> Image.Image:
    """CLAHE (Contrast Limited Adaptive Histogram Equalization) で光の正規化を行う。

    照明条件の違いによる明暗差を軽減し、埋め込みベクトルの安定性を向上させる。
    LAB色空間のL(輝度)チャネルにのみCLAHEを適用し、色情報は保持する。

    Args:
        img: 入力画像 (RGB)

    Returns:
        光正規化済み画像 (RGB)
    """
    import cv2

    arr = np.array(img)

    # RGB → LAB
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)

    # L チャネルに CLAHE 適用
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])

    # LAB → RGB
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    return Image.fromarray(out)
