/**
 * UI 設定値。表示の微調整をここで一元管理する。
 */

// ─── BBox オーバーレイ ────────────────────────────────────────
/** BBox 枠線の太さ (px) */
export const BBOX_BORDER_WIDTH = 4;

// ─── カメラ ───────────────────────────────────────────────────
/** ガイド枠の太さ (px) */
export const GUIDE_BORDER_WIDTH = 2;

// ─── フレームキャプチャ ───────────────────────────────────────
/** キャプチャ間隔 (ms) */
export const CAPTURE_INTERVAL_MS = 100;
/** 最大フレーム数 */
export const MAX_FRAMES = 10;
/** 類似度閾値 (これ以上類似していたら除外) */
export const SIMILARITY_THRESHOLD = 0.97;
