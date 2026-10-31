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
  console.log("[upload] uploadImage 開始:", {
    size: blob.size,
    type: blob.type,
    filename,
  });

  // 1. presigned URL 取得
  console.log("[upload] Step 1: presigned URL 取得中...");
  const { upload_url, key } = await getUploadUrl(
    blob.type || "image/jpeg",
    filename || `frame_${Date.now()}.jpg`
  );
  console.log("[upload] presigned URL 取得完了:", { key, urlPrefix: upload_url.slice(0, 80) + "..." });

  // 2. S3 に直接 PUT
  console.log("[upload] Step 2: S3 PUT 実行中...", {
    contentType: blob.type || "image/jpeg",
    blobSize: blob.size,
  });
  const res = await fetch(upload_url, {
    method: "PUT",
    headers: {
      "Content-Type": blob.type || "image/jpeg",
    },
    body: blob,
  });

  if (!res.ok) {
    console.error("[upload] S3 PUT 失敗:", res.status, res.statusText);
    throw new Error(`S3 upload failed: ${res.status} ${res.statusText}`);
  }

  console.log("[upload] S3 PUT 成功:", { key, status: res.status });
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
  console.log("[upload] uploadImages 開始:", { total });

  // 並列度制限 (3並列)
  const CONCURRENCY = 3;

  for (let i = 0; i < total; i += CONCURRENCY) {
    const batch = blobs.slice(i, i + CONCURRENCY);
    console.log(`[upload] バッチ ${Math.floor(i / CONCURRENCY) + 1}: ${batch.length} 枚`);
    const batchResults = await Promise.all(
      batch.map((blob, idx) =>
        uploadImage(blob, `frame_${Date.now()}_${i + idx}.jpg`)
      )
    );
    results.push(...batchResults);
    completed += batch.length;
    console.log(`[upload] 進捗: ${completed}/${total}`);
    onProgress?.(completed, total);
  }

  console.log("[upload] uploadImages 完了:", { total: results.length });
  return results;
}
