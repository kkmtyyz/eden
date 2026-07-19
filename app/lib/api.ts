/**
 * Eden API クライアント。
 * Cognito の ID Token を Authorization ヘッダーに付与してリクエストする。
 */

import { fetchAuthSession } from "aws-amplify/auth";
import { getApiEndpoint } from "../amplify-config";

async function getAuthHeaders(): Promise<Record<string, string>> {
  console.log("[api] 認証セッション取得中...");
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString();
  console.log("[api] トークン取得:", token ? "OK" : "なし (未ログイン?)");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: token } : {}),
  };
}

async function apiRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const endpoint = getApiEndpoint();
  if (!endpoint) {
    throw new Error("API endpoint not configured. Run 'npx ampx sandbox'.");
  }

  const headers = await getAuthHeaders();
  const url = `${endpoint}${path}`;

  console.log(`[api] ${options.method || "GET"} ${path}`);
  console.log("[api] URL:", url);

  const res = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers || {}) },
  });

  console.log(`[api] レスポンス: ${res.status} ${res.statusText}`);

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    console.error("[api] エラーレスポンス:", body);
    throw new Error(body.error || `API error: ${res.status}`);
  }

  const data = await res.json();
  console.log(`[api] ${path} 成功:`, data);
  return data;
}

// ─── API メソッド ─────────────────────────────────────────────

export interface UploadUrlResponse {
  upload_url: string;
  key: string;
  expires_in: number;
}

export function getUploadUrl(
  contentType = "image/jpeg",
  filename = "image.jpg"
): Promise<UploadUrlResponse> {
  console.log("[api] getUploadUrl 呼び出し:", { contentType, filename });
  return apiRequest("/upload-url", {
    method: "POST",
    body: JSON.stringify({ content_type: contentType, filename }),
  });
}

export interface BboxCandidatesResponse {
  boxes: number[][];
  default_index: number | null;
  image_size: number[];
}

export function getBboxCandidates(key: string): Promise<BboxCandidatesResponse> {
  console.log("[api] getBboxCandidates 呼び出し:", { key });
  return apiRequest("/bbox-candidates", {
    method: "POST",
    body: JSON.stringify({ key }),
  });
}

export interface RegisterResponse {
  job_id: string;
}

export function registerObject(
  key: string,
  box: number[],
  objectId?: string
): Promise<RegisterResponse> {
  console.log("[api] registerObject 呼び出し:", { key, box, objectId });
  return apiRequest("/register", {
    method: "POST",
    body: JSON.stringify({ key, box, object_id: objectId }),
  });
}

export interface JobStatusResponse {
  job_id: string;
  status: "pending" | "processing" | "completed" | "failed";
  object_id: string | null;
  created_at: string;
  error: string | null;
}

export function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  console.log("[api] getJobStatus 呼び出し:", { jobId });
  return apiRequest(`/jobs/${jobId}`, { method: "GET" });
}

export interface SearchResultItem {
  box: number[];
  verdict: "green" | "yellow" | "red";
  object_id: string | null;
  sim: number;
  margin: number;
  votes: number;
  text_hit: boolean;
  object_info: {
    object_id: string;
    thumbnail_key: string | null;
    info: Array<{ text: string; user_id: string; created_at: string }>;
  } | null;
}

export interface SearchResponse {
  results: SearchResultItem[];
}

export function searchImage(
  key: string,
  boxes?: number[][]
): Promise<SearchResponse> {
  console.log("[api] searchImage 呼び出し:", { key, boxes });
  return apiRequest("/search", {
    method: "POST",
    body: JSON.stringify({ key, boxes }),
  });
}

export interface AddInfoResponse {
  message: string;
  object_id: string;
}

export function addObjectInfo(
  objectId: string,
  text: string
): Promise<AddInfoResponse> {
  console.log("[api] addObjectInfo 呼び出し:", { objectId, text });
  return apiRequest(`/objects/${objectId}/info`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}
