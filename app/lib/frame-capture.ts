/**
 * フレームキャプチャ＆プルーニング。
 *
 * - 100ms 間隔でフレームをキャプチャ
 * - 類似フレームを画素差分で除去（ブレ or 静止判定）
 * - 上限 MAX_FRAMES 枚に制限
 */

const CAPTURE_INTERVAL_MS = 100;
const MAX_FRAMES = 10;
const SIMILARITY_THRESHOLD = 0.97; // これ以上類似していたら除外

export interface CapturedFrame {
  blob: Blob;
  timestamp: number;
  /** プルーニング時の比較用: ダウンサンプル済みピクセルデータ */
  fingerprint: Uint8Array;
}

/**
 * Blob → ImageData (縮小サイズ) を取得。
 * 比較用に 64x64 にリサイズして差分計算を高速化。
 */
async function blobToFingerprint(blob: Blob): Promise<Uint8Array> {
  const bitmap = await createImageBitmap(blob);
  const canvas = new OffscreenCanvas(64, 64);
  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(bitmap, 0, 0, 64, 64);
  bitmap.close();
  const imageData = ctx.getImageData(0, 0, 64, 64);
  // グレースケール化して 64x64 = 4096 バイトの fingerprint
  const gray = new Uint8Array(64 * 64);
  for (let i = 0; i < gray.length; i++) {
    const r = imageData.data[i * 4];
    const g = imageData.data[i * 4 + 1];
    const b = imageData.data[i * 4 + 2];
    gray[i] = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
  }
  return gray;
}

/**
 * 2 つの fingerprint の類似度を 0〜1 で返す（1 = 完全一致）。
 * 正規化相関 (NCC) を使用。
 */
function similarity(a: Uint8Array, b: Uint8Array): number {
  const n = a.length;
  let sumA = 0,
    sumB = 0;
  for (let i = 0; i < n; i++) {
    sumA += a[i];
    sumB += b[i];
  }
  const meanA = sumA / n;
  const meanB = sumB / n;

  let num = 0,
    denA = 0,
    denB = 0;
  for (let i = 0; i < n; i++) {
    const da = a[i] - meanA;
    const db = b[i] - meanB;
    num += da * db;
    denA += da * da;
    denB += db * db;
  }

  const den = Math.sqrt(denA * denB);
  if (den === 0) return 1; // 完全に同じ画像
  return num / den;
}

export type CaptureCallback = () => Blob | null;

export interface FrameCaptureSession {
  /** キャプチャ開始 */
  start: () => void;
  /** キャプチャ停止して結果を返す（プルーニング済み） */
  stop: () => Promise<CapturedFrame[]>;
  /** 現在キャプチャ中か */
  isCapturing: boolean;
  /** 現在のフレーム数（生） */
  rawCount: number;
}

/**
 * フレームキャプチャセッションを作成。
 * captureFn は Camera コンポーネントの captureFrame を渡す。
 */
export function createFrameCaptureSession(
  captureFn: CaptureCallback
): FrameCaptureSession {
  let intervalId: ReturnType<typeof setInterval> | null = null;
  let rawFrames: { blob: Blob; timestamp: number }[] = [];
  let capturing = false;

  const session: FrameCaptureSession = {
    get isCapturing() {
      return capturing;
    },
    get rawCount() {
      return rawFrames.length;
    },

    start() {
      rawFrames = [];
      capturing = true;
      intervalId = setInterval(() => {
        const blob = captureFn();
        if (blob) {
          rawFrames.push({ blob, timestamp: Date.now() });
        }
      }, CAPTURE_INTERVAL_MS);
    },

    async stop(): Promise<CapturedFrame[]> {
      capturing = false;
      if (intervalId) {
        clearInterval(intervalId);
        intervalId = null;
      }

      if (rawFrames.length === 0) return [];

      // fingerprint 計算
      const frames: CapturedFrame[] = await Promise.all(
        rawFrames.map(async (f) => ({
          blob: f.blob,
          timestamp: f.timestamp,
          fingerprint: await blobToFingerprint(f.blob),
        }))
      );

      // プルーニング: 類似フレームを除去
      return pruneFrames(frames);
    },
  };

  return session;
}

/**
 * 類似フレームを除去し上限 MAX_FRAMES に絞る。
 * 戦略: 先頭から順に保持し、既に保持したフレームと類似していたら除外。
 * MAX_FRAMES を超えたら等間隔サンプリング。
 */
function pruneFrames(frames: CapturedFrame[]): CapturedFrame[] {
  if (frames.length <= 1) return frames;

  // 1. 類似フレーム除去
  const kept: CapturedFrame[] = [frames[0]];
  for (let i = 1; i < frames.length; i++) {
    const isTooSimilar = kept.some(
      (k) => similarity(k.fingerprint, frames[i].fingerprint) > SIMILARITY_THRESHOLD
    );
    if (!isTooSimilar) {
      kept.push(frames[i]);
    }
  }

  // 2. MAX_FRAMES 以下に制限（等間隔サンプリング）
  if (kept.length <= MAX_FRAMES) return kept;

  const step = (kept.length - 1) / (MAX_FRAMES - 1);
  const sampled: CapturedFrame[] = [];
  for (let i = 0; i < MAX_FRAMES; i++) {
    sampled.push(kept[Math.round(i * step)]);
  }
  return sampled;
}
