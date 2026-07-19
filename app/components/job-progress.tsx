"use client";

import { useEffect, useRef, useState } from "react";
import { getJobStatus, JobStatusResponse } from "../lib/api";

interface JobProgressProps {
  jobId: string;
  onComplete: () => void;
  onError: (message: string) => void;
  /** ポーリング間隔 (ms), default: 3000 */
  pollInterval?: number;
}

/**
 * ジョブ進捗ポーリングコンポーネント。
 * - GET /jobs/{id} を定期的に呼び出し
 * - completed → onComplete
 * - failed → onError
 */
export default function JobProgress({
  jobId,
  onComplete,
  onError,
  pollInterval = 3000,
}: JobProgressProps) {
  const [status, setStatus] = useState<JobStatusResponse["status"]>("pending");
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    console.log("[job-progress] ポーリング開始:", { jobId, pollInterval });

    // 経過時間カウンター
    const startTime = Date.now();
    timerRef.current = setInterval(() => {
      if (mountedRef.current) {
        setElapsed(Math.floor((Date.now() - startTime) / 1000));
      }
    }, 1000);

    // ポーリング
    const poll = async () => {
      try {
        const res = await getJobStatus(jobId);
        console.log("[job-progress] ステータス:", res.status);
        if (!mountedRef.current) return;

        setStatus(res.status);

        if (res.status === "completed") {
          cleanup();
          onComplete();
        } else if (res.status === "failed") {
          cleanup();
          onError(res.error || "登録処理が失敗しました");
        }
      } catch (err) {
        console.error("[job-progress] ポーリングエラー:", err);
        // ネットワークエラーでは停止せず継続
      }
    };

    // 即時実行 + 定期実行
    poll();
    pollRef.current = setInterval(poll, pollInterval);

    function cleanup() {
      if (timerRef.current) clearInterval(timerRef.current);
      if (pollRef.current) clearInterval(pollRef.current);
    }

    return () => {
      mountedRef.current = false;
      cleanup();
    };
  }, [jobId, pollInterval, onComplete, onError]);

  const statusLabel: Record<string, string> = {
    pending: "待機中...",
    processing: "処理中...",
    completed: "完了!",
    failed: "失敗",
  };

  return (
    <div className="flex flex-col items-center gap-4">
      {/* スピナー */}
      {(status === "pending" || status === "processing") && (
        <div className="w-12 h-12 border-4 border-white/20 border-t-blue-500 rounded-full animate-spin" />
      )}

      {/* ステータス */}
      <p className="text-base font-medium">
        {statusLabel[status] || status}
      </p>

      {/* 詳細 */}
      <div className="text-xs text-white/50 text-center space-y-1">
        <p>ジョブ ID: {jobId.slice(0, 8)}...</p>
        <p>経過: {elapsed}秒</p>
        {status === "processing" && (
          <p className="text-white/40">
            BBox追跡 → クロップ → 文字抽出 → 埋め込み → 登録
          </p>
        )}
      </div>
    </div>
  );
}
