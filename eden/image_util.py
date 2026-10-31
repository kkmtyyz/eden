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
    OpenCV 不要の numpy 実装。

    Args:
        img: 入力画像 (RGB)

    Returns:
        光正規化済み画像 (RGB)
    """
    arr = np.array(img, dtype=np.float32)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # 輝度チャネル (ITU-R BT.601)
    lum = (0.299 * r + 0.587 * g + 0.114 * b).astype(np.uint8)

    # CLAHE 適用
    lum_eq = _clahe_fast(lum, clip_limit=2.0, n_tiles=8)

    # 輝度比率でRGB各チャネルを補正
    lum_f = lum.astype(np.float32) + 1.0  # ゼロ除算防止
    ratio = lum_eq.astype(np.float32) / lum_f

    out = np.stack([
        np.clip(r * ratio, 0, 255).astype(np.uint8),
        np.clip(g * ratio, 0, 255).astype(np.uint8),
        np.clip(b * ratio, 0, 255).astype(np.uint8),
    ], axis=-1)

    return Image.fromarray(out)


def _clahe_fast(gray: np.ndarray, clip_limit: float = 2.0, n_tiles: int = 8) -> np.ndarray:
    """numpy ベースの高速 CLAHE 実装。

    タイルごとにヒストグラム均等化し、タイル間はバイリニア補間で滑らかに接続する。
    """
    h, w = gray.shape
    tile_h = h // n_tiles
    tile_w = w // n_tiles

    # パディングしてタイルで割り切れるようにする
    pad_h = tile_h * n_tiles
    pad_w = tile_w * n_tiles
    gray_padded = gray[:pad_h, :pad_w]

    # 各タイルの LUT (Look-Up Table) を計算
    luts = np.zeros((n_tiles, n_tiles, 256), dtype=np.float32)

    for ty in range(n_tiles):
        for tx in range(n_tiles):
            tile = gray_padded[ty * tile_h:(ty + 1) * tile_h, tx * tile_w:(tx + 1) * tile_w]
            hist, _ = np.histogram(tile, bins=256, range=(0, 256))

            # クリッピング
            n_pixels = tile.size
            clip_val = max(1, int(clip_limit * n_pixels / 256))
            excess = np.sum(np.maximum(hist - clip_val, 0))
            hist = np.minimum(hist, clip_val)
            hist = hist + excess // 256

            # CDF → LUT
            cdf = hist.cumsum().astype(np.float32)
            cdf_min = cdf[cdf > 0].min() if np.any(cdf > 0) else 0
            denom = n_pixels - cdf_min
            if denom > 0:
                luts[ty, tx] = ((cdf - cdf_min) / denom * 255.0).clip(0, 255)
            else:
                luts[ty, tx] = np.arange(256, dtype=np.float32)

    # ピクセルごとにバイリニア補間で LUT 適用 (ベクトル化)
    out = np.zeros((pad_h, pad_w), dtype=np.uint8)

    # 各ピクセルのタイル座標と補間重み
    ys = np.arange(pad_h)
    xs = np.arange(pad_w)

    # タイル中心基準の座標
    fy = (ys / tile_h) - 0.5
    fx = (xs / tile_w) - 0.5

    ty0 = np.clip(np.floor(fy).astype(int), 0, n_tiles - 1)
    tx0 = np.clip(np.floor(fx).astype(int), 0, n_tiles - 1)
    ty1 = np.minimum(ty0 + 1, n_tiles - 1)
    tx1 = np.minimum(tx0 + 1, n_tiles - 1)

    ya = np.clip(fy - ty0, 0, 1)
    xa = np.clip(fx - tx0, 0, 1)

    # 行ごとに処理（完全ベクトル化は困難なのでx軸をベクトル化）
    for y in range(pad_h):
        row_vals = gray_padded[y, :]  # (pad_w,)
        t0y = ty0[y]
        t1y = ty1[y]
        ay = ya[y]

        v00 = luts[t0y, tx0, row_vals]  # (pad_w,)
        v01 = luts[t0y, tx1, row_vals]
        v10 = luts[t1y, tx0, row_vals]
        v11 = luts[t1y, tx1, row_vals]

        top = v00 * (1 - xa) + v01 * xa
        bot = v10 * (1 - xa) + v11 * xa
        out[y, :] = (top * (1 - ay) + bot * ay).astype(np.uint8)

    # 元のサイズに合わせて返す
    result = np.zeros_like(gray)
    result[:pad_h, :pad_w] = out
    # パディング外（あれば）はオリジナル値を使用
    if h > pad_h:
        result[pad_h:, :] = gray[pad_h:, :]
    if w > pad_w:
        result[:, pad_w:] = gray[:, pad_w:]
    return result
