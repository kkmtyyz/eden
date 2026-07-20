"use client";

import { useState, useCallback, useEffect } from "react";
import { SearchResultItem, addObjectInfo, getDownloadUrl, registerObject, getJobStatus } from "../lib/api";
import { BBOX_BORDER_WIDTH } from "../lib/ui-config";

interface SearchResultProps {
  imageUrl: string;
  imageWidth: number;
  imageHeight: number;
  results: SearchResultItem[];
  imageKey: string;
}

const VERDICT_BORDER: Record<string, string> = {
  green: "border-solid border-green-500 shadow-[0_0_6px_rgba(34,197,94,0.5)]",
  yellow: "border-solid border-yellow-500 shadow-[0_0_6px_rgba(234,179,8,0.5)]",
  red: "border-solid border-red-500 shadow-[0_0_6px_rgba(239,68,68,0.5)]",
};

export default function SearchResult({
  imageUrl,
  imageWidth,
  imageHeight,
  results,
  imageKey,
}: SearchResultProps) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const selected = selectedIdx !== null ? results[selectedIdx] : null;

  return (
    <div className="flex flex-col">
      {/* 画像 + BBox オーバーレイ */}
      <div className="relative w-full">
        <img src={imageUrl} alt="検索画像" className="w-full block" />
        {results.map((item, idx) => {
          const [x1, y1, x2, y2] = item.box;
          // BBox は 0-1000 正規化スケールなので 1000 で割る
          const style = {
            left: `${(x1 / 1000) * 100}%`,
            top: `${(y1 / 1000) * 100}%`,
            width: `${((x2 - x1) / 1000) * 100}%`,
            height: `${((y2 - y1) / 1000) * 100}%`,
            borderWidth: `${BBOX_BORDER_WIDTH}px`,
          };
          return (
            <button
              key={idx}
              className={`absolute rounded bg-transparent p-0 cursor-pointer active:bg-white/10 ${VERDICT_BORDER[item.verdict]}`}
              style={style}
              onClick={() => setSelectedIdx(idx)}
              aria-label={`検出物体 ${idx + 1}: ${verdictLabel(item.verdict)}`}
            />
          );
        })}
      </div>

      {/* ヒント */}
      {results.length === 0 && (
        <p className="text-center p-4 text-sm text-white/60">
          物体が検出されませんでした
        </p>
      )}
      {results.length > 0 && !selected && (
        <p className="text-center p-4 text-sm text-white/60">
          枠をタップして詳細を確認してください
        </p>
      )}

      {/* 詳細パネル */}
      {selected && (
        <DetailPanel item={selected} imageKey={imageKey} onClose={() => setSelectedIdx(null)} />
      )}
    </div>
  );
}

// ─── DetailPanel ──────────────────────────────────────────────

interface DetailPanelProps {
  item: SearchResultItem;
  imageKey: string;
  onClose: () => void;
}

function DetailPanel({ item, imageKey, onClose }: DetailPanelProps) {
  switch (item.verdict) {
    case "green":
      return <GreenPanel item={item} onClose={onClose} />;
    case "yellow":
      return <YellowPanel item={item} imageKey={imageKey} onClose={onClose} />;
    case "red":
      return <RedPanel onClose={onClose} />;
  }
}

// ─── Green: 既知物体 → 情報表示＋追加 ─────────────────────────

function GreenPanel({
  item,
  onClose,
}: {
  item: SearchResultItem;
  onClose: () => void;
}) {
  const [infoText, setInfoText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [infoList, setInfoList] = useState(item.object_info?.info || []);
  const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(null);
  const [thumbnailLoading, setThumbnailLoading] = useState(false);

  useEffect(() => {
    const key = item.object_info?.thumbnail_key;
    if (!key) return;
    let cancelled = false;
    setThumbnailLoading(true);
    getDownloadUrl(key)
      .then((res) => {
        if (!cancelled) setThumbnailUrl(res.download_url);
      })
      .catch((err) => {
        console.error("[GreenPanel] サムネイル取得失敗:", err);
      })
      .finally(() => {
        if (!cancelled) setThumbnailLoading(false);
      });
    return () => { cancelled = true; };
  }, [item.object_info?.thumbnail_key]);

  const handleAddInfo = useCallback(async () => {
    if (!infoText.trim() || !item.object_id) return;
    setSubmitting(true);
    try {
      await addObjectInfo(item.object_id, infoText.trim());
      setInfoList((prev) => [
        ...prev,
        { text: infoText.trim(), user_id: "me", created_at: new Date().toISOString() },
      ]);
      setInfoText("");
    } catch {
      // TODO: トースト通知
    } finally {
      setSubmitting(false);
    }
  }, [infoText, item.object_id]);

  return (
    <div className="fixed bottom-14 left-0 right-0 max-h-[50dvh] overflow-y-auto z-[90] bg-neutral-900/95 backdrop-blur-md border-t-[3px] border-green-500 p-4 rounded-t-xl animate-slide-up">
      {/* ヘッダー */}
      <div className="flex items-center gap-3 mb-3">
        <span className="px-2 py-0.5 rounded bg-green-500 text-black text-xs font-semibold">
          一致
        </span>
        <span className="text-xs text-white/60">
          類似度: {(item.sim * 100).toFixed(1)}%
        </span>
        <button onClick={onClose} className="ml-auto text-white/60 text-xl p-1">
          ✕
        </button>
      </div>

      {item.object_info?.thumbnail_key && (
        <div className="mb-3">
          {thumbnailLoading && (
            <div className="w-full h-24 bg-white/5 rounded-lg flex items-center justify-center">
              <span className="text-xs text-white/40">読み込み中...</span>
            </div>
          )}
          {thumbnailUrl && !thumbnailLoading && (
            <img
              src={thumbnailUrl}
              alt="登録済み物体のサムネイル"
              className="w-full max-h-36 object-contain rounded-lg border border-white/10"
            />
          )}
          <p className="text-xs text-white/50 mt-1">
            登録済み物体 (ID: {item.object_id?.slice(0, 8)}...)
          </p>
        </div>
      )}

      {/* 既存情報一覧 */}
      <div className="mb-3 max-h-[120px] overflow-y-auto">
        {infoList.length === 0 && (
          <p className="text-xs text-white/40">まだ情報がありません</p>
        )}
        {infoList.map((info, i) => (
          <div key={i} className="py-2 border-b border-white/10">
            <p className="text-sm">{info.text}</p>
            <small className="text-[0.7rem] text-white/40">
              {new Date(info.created_at).toLocaleDateString("ja-JP")}
            </small>
          </div>
        ))}
      </div>

      {/* 情報追加フォーム */}
      <div className="flex gap-2">
        <input
          type="text"
          value={infoText}
          onChange={(e) => setInfoText(e.target.value)}
          placeholder="この物体の情報を追加..."
          className="flex-1 px-3 py-2 bg-white/10 border border-white/20 rounded-md text-white text-sm placeholder:text-white/40"
          disabled={submitting}
        />
        <button
          className="px-4 py-2 bg-green-500 text-black rounded-md text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={handleAddInfo}
          disabled={submitting || !infoText.trim()}
        >
          {submitting ? "..." : "追加"}
        </button>
      </div>
    </div>
  );
}

// ─── Yellow: 要確認 → サムネ提示 ──────────────────────────────

function YellowPanel({
  item,
  imageKey,
  onClose,
}: {
  item: SearchResultItem;
  imageKey: string;
  onClose: () => void;
}) {
  const [confirmed, setConfirmed] = useState<boolean | null>(null);
  const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(null);
  const [thumbnailLoading, setThumbnailLoading] = useState(false);
  const [voteStatus, setVoteStatus] = useState<"idle" | "registering" | "polling" | "done" | "error">("idle");
  const [voteError, setVoteError] = useState<string | null>(null);

  useEffect(() => {
    const key = item.object_info?.thumbnail_key;
    if (!key) return;
    let cancelled = false;
    setThumbnailLoading(true);
    getDownloadUrl(key)
      .then((res) => {
        if (!cancelled) setThumbnailUrl(res.download_url);
      })
      .catch((err) => {
        console.error("[YellowPanel] サムネイル取得失敗:", err);
      })
      .finally(() => {
        if (!cancelled) setThumbnailLoading(false);
      });
    return () => { cancelled = true; };
  }, [item.object_info?.thumbnail_key]);

  const handleVoteYes = useCallback(async () => {
    if (!item.object_id) return;
    setConfirmed(true);
    setVoteStatus("registering");

    try {
      // 検索画像をそのまま1枚で追加登録 (既存object_idを指定)
      console.log("[YellowPanel] 投票: registerObject 呼び出し", {
        imageKey, box: item.box, object_id: item.object_id,
      });
      const { job_id } = await registerObject(
        [imageKey],
        imageKey,
        item.box,
        item.object_id
      );
      console.log("[YellowPanel] ジョブ投入完了: job_id=%s", job_id);

      // ポーリング
      setVoteStatus("polling");
      const maxAttempts = 40; // 最大 ~2分
      for (let i = 0; i < maxAttempts; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const status = await getJobStatus(job_id);
        console.log("[YellowPanel] ジョブ状態:", status.status);
        if (status.status === "completed") {
          setVoteStatus("done");
          return;
        }
        if (status.status === "failed") {
          setVoteStatus("error");
          setVoteError(status.error || "登録に失敗しました");
          return;
        }
      }
      // タイムアウト
      setVoteStatus("error");
      setVoteError("登録がタイムアウトしました");
    } catch (err) {
      console.error("[YellowPanel] 投票エラー:", err);
      setVoteStatus("error");
      setVoteError(err instanceof Error ? err.message : "エラーが発生しました");
    }
  }, [item.object_id, item.box, imageKey]);

  return (
    <div className="fixed bottom-14 left-0 right-0 max-h-[50dvh] overflow-y-auto z-[90] bg-neutral-900/95 backdrop-blur-md border-t-[3px] border-yellow-500 p-4 rounded-t-xl animate-slide-up">
      <div className="flex items-center gap-3 mb-3">
        <span className="px-2 py-0.5 rounded bg-yellow-500 text-black text-xs font-semibold">
          要確認
        </span>
        <span className="text-xs text-white/60">
          類似度: {(item.sim * 100).toFixed(1)}%
        </span>
        <button onClick={onClose} className="ml-auto text-white/60 text-xl p-1">
          ✕
        </button>
      </div>

      <p className="text-sm mb-3">この物体ですか？</p>

      {/* サムネイル画像 */}
      {item.object_info?.thumbnail_key && (
        <div className="mb-3">
          {thumbnailLoading && (
            <div className="w-full h-32 bg-white/5 rounded-lg flex items-center justify-center">
              <span className="text-xs text-white/40">読み込み中...</span>
            </div>
          )}
          {thumbnailUrl && !thumbnailLoading && (
            <img
              src={thumbnailUrl}
              alt="候補物体のサムネイル"
              className="w-full max-h-48 object-contain rounded-lg border border-white/10"
            />
          )}
          <p className="text-xs text-white/50 mt-1">
            候補物体 ID: {item.object_id?.slice(0, 8)}...
          </p>
        </div>
      )}

      {confirmed === null && (
        <div className="flex gap-4 mt-2">
          <button
            className="flex-1 py-2.5 bg-green-500 text-black rounded-lg text-sm font-semibold"
            onClick={handleVoteYes}
          >
            はい
          </button>
          <button
            className="flex-1 py-2.5 bg-white/15 text-white rounded-lg text-sm font-semibold"
            onClick={() => setConfirmed(false)}
          >
            いいえ
          </button>
        </div>
      )}

      {confirmed === true && voteStatus === "registering" && (
        <div className="flex items-center gap-2 mt-2">
          <div className="w-4 h-4 border-2 border-white/20 border-t-yellow-500 rounded-full animate-spin" />
          <p className="text-sm text-white/70">ベクトル登録中...</p>
        </div>
      )}
      {confirmed === true && voteStatus === "polling" && (
        <div className="flex items-center gap-2 mt-2">
          <div className="w-4 h-4 border-2 border-white/20 border-t-yellow-500 rounded-full animate-spin" />
          <p className="text-sm text-white/70">処理中...</p>
        </div>
      )}
      {confirmed === true && voteStatus === "done" && (
        <p className="text-sm text-green-400 mt-2">
          ✓ 正解として登録しました。次回から精度が向上します。
        </p>
      )}
      {confirmed === true && voteStatus === "error" && (
        <p className="text-sm text-red-400 mt-2">
          登録に失敗しました: {voteError}
        </p>
      )}
      {confirmed === false && (
        <p className="text-sm text-white/70 mt-2">
          別の物体として記録しました。
        </p>
      )}
    </div>
  );
}

// ─── Red: 未知物体 → 登録導線 ─────────────────────────────────

function RedPanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed bottom-14 left-0 right-0 max-h-[50dvh] overflow-y-auto z-[90] bg-neutral-900/95 backdrop-blur-md border-t-[3px] border-red-500 p-4 rounded-t-xl animate-slide-up">
      <div className="flex items-center gap-3 mb-3">
        <span className="px-2 py-0.5 rounded bg-red-500 text-white text-xs font-semibold">
          未登録
        </span>
        <button onClick={onClose} className="ml-auto text-white/60 text-xl p-1">
          ✕
        </button>
      </div>
      <p className="text-sm mb-4">この物体はまだ登録されていません。</p>
      <button
        className="w-full py-3 bg-blue-600 text-white rounded-lg text-sm font-semibold"
        onClick={() => {
          window.location.href = "/register";
        }}
      >
        登録する
      </button>
    </div>
  );
}

// ─── ユーティリティ ──────────────────────────────────────────

function verdictLabel(v: string): string {
  switch (v) {
    case "green": return "一致";
    case "yellow": return "要確認";
    case "red": return "未登録";
    default: return v;
  }
}
