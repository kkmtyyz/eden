/**
 * Eden API クライアント。
 * Cognito の ID Token を Authorization ヘッダーに付与してリクエストする。
 */

import { fetchAuthSession } from "aws-amplify/auth";
import { getApiEndpoint } from "../amplify-config";

async function getAuthHeaders(): Promise<Record<string, string>> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString();
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

  const res = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers || {}) },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `API error: ${res.status}`);
  }

  return res.json();
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
  return apiRequest(`/objects/${objectId}/info`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}
