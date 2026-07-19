/**
 * S3 presigned PUT を使ったアップロード処理。
 *
 * フロー:
 * 1. POST /upload-url で presigned URL + S3 key を取得
 * 2. presigned URL に直接 PUT で画像をアップロード
 */

import { getUploadUrl } from "./api";

export interface UploadResult {
  /** S3 上のキー */
  key: string;
  /** アップロード先 URL (確認用) */
  url: string;
}

/**
 * 単一画像を presigned PUT でアップロード。
 */
export async function uploadImage(
  blob: Blob,
  filename?: string
): Promise<UploadResult> {
  // 1. presigned URL 取得
  const { upload_url, key } = await getUploadUrl(
    blob.type || "image/jpeg",
    filename || `frame_${Date.now()}.jpg`
  );

  // 2. S3 に直接 PUT
  const res = await fetch(upload_url, {
    method: "PUT",
    headers: {
      "Content-Type": blob.type || "image/jpeg",
    },
    body: blob,
  });

  if (!res.ok) {
    throw new Error(`S3 upload failed: ${res.status} ${res.statusText}`);
  }

  return { key, url: upload_url };
}

/**
 * 複数画像を並列アップロード。
 * 進捗コールバック付き。
 */
export async function uploadImages(
  blobs: Blob[],
  onProgress?: (completed: number, total: number) => void
): Promise<UploadResult[]> {
  const total = blobs.length;
  let completed = 0;
  const results: UploadResult[] = [];

  // 並列度制限 (3並列)
  const CONCURRENCY = 3;

  for (let i = 0; i < total; i += CONCURRENCY) {
    const batch = blobs.slice(i, i + CONCURRENCY);
    const batchResults = await Promise.all(
      batch.map((blob, idx) =>
        uploadImage(blob, `frame_${Date.now()}_${i + idx}.jpg`)
      )
    );
    results.push(...batchResults);
    completed += batch.length;
    onProgress?.(completed, total);
  }

  return results;
}
