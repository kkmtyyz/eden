"use client";

import { useState, useCallback } from "react";
import { SearchResultItem, addObjectInfo } from "../lib/api";

interface SearchResultProps {
  imageUrl: string;
  imageWidth: number;
  imageHeight: number;
  results: SearchResultItem[];
}

/**
 * 検索結果オーバーレイ表示。
 * - 画像上に BBox を色分けして表示（緑/黄/赤）
 * - BBox タップで詳細パネルを表示
 * - 緑: 情報表示＋追加
 * - 黄: サムネ提示で確認
 * - 赤: 新規登録導線
 */
export default function SearchResult({
  imageUrl,
  imageWidth,
  imageHeight,
  results,
}: SearchResultProps) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  const selected = selectedIdx !== null ? results[selectedIdx] : null;

  return (
    <div className="search-result">
      {/* 画像 + BBox オーバーレイ */}
      <div className="search-result-image-container">
        <img
          src={imageUrl}
          alt="検索画像"
          className="search-result-image"
        />
        {/* BBox オーバーレイ */}
        {results.map((item, idx) => {
          const [x1, y1, x2, y2] = item.box;
          const style = {
            left: `${(x1 / imageWidth) * 100}%`,
            top: `${(y1 / imageHeight) * 100}%`,
            width: `${((x2 - x1) / imageWidth) * 100}%`,
            height: `${((y2 - y1) / imageHeight) * 100}%`,
          };
          return (
            <button
              key={idx}
              className={`bbox-overlay bbox-${item.verdict}`}
              style={style}
              onClick={() => setSelectedIdx(idx)}
              aria-label={`検出物体 ${idx + 1}: ${verdictLabel(item.verdict)}`}
            />
          );
        })}
      </div>

      {/* 判定サマリー */}
      {results.length === 0 && (
        <p className="search-result-empty">物体が検出されませんでした</p>
      )}

      {results.length > 0 && !selected && (
        <p className="search-result-hint">
          枠をタップして詳細を確認してください
        </p>
      )}

      {/* 詳細パネル */}
      {selected && (
        <DetailPanel
          item={selected}
          onClose={() => setSelectedIdx(null)}
        />
      )}
    </div>
  );
}

// ─── DetailPanel ──────────────────────────────────────────────

interface DetailPanelProps {
  item: SearchResultItem;
  onClose: () => void;
}

function DetailPanel({ item, onClose }: DetailPanelProps) {
  switch (item.verdict) {
    case "green":
      return <GreenPanel item={item} onClose={onClose} />;
    case "yellow":
      return <YellowPanel item={item} onClose={onClose} />;
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
  const [infoList, setInfoList] = useState(
    item.object_info?.info || []
  );

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
      // エラーは握りつぶさずトースト等で出すべきだが暫定
    } finally {
      setSubmitting(false);
    }
  }, [infoText, item.object_id]);

  return (
    <div className="detail-panel detail-panel-green">
      <div className="detail-panel-header">
        <span className="verdict-badge verdict-green">一致</span>
        <span className="detail-sim">類似度: {(item.sim * 100).toFixed(1)}%</span>
        <button className="detail-close" onClick={onClose}>✕</button>
      </div>

      {/* サムネイル */}
      {item.object_info?.thumbnail_key && (
        <p className="detail-thumbnail-note">
          登録済み物体 (ID: {item.object_id?.slice(0, 8)}...)
        </p>
      )}

      {/* 既存情報一覧 */}
      <div className="detail-info-list">
        {infoList.length === 0 && (
          <p className="detail-no-info">まだ情報がありません</p>
        )}
        {infoList.map((info, i) => (
          <div key={i} className="detail-info-item">
            <p>{info.text}</p>
            <small>{new Date(info.created_at).toLocaleDateString("ja-JP")}</small>
          </div>
        ))}
      </div>

      {/* 情報追加フォーム */}
      <div className="detail-add-info">
        <input
          type="text"
          value={infoText}
          onChange={(e) => setInfoText(e.target.value)}
          placeholder="この物体の情報を追加..."
          className="detail-input"
          disabled={submitting}
        />
        <button
          className="detail-submit"
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
  onClose,
}: {
  item: SearchResultItem;
  onClose: () => void;
}) {
  const [confirmed, setConfirmed] = useState<boolean | null>(null);

  return (
    <div className="detail-panel detail-panel-yellow">
      <div className="detail-panel-header">
        <span className="verdict-badge verdict-yellow">要確認</span>
        <span className="detail-sim">類似度: {(item.sim * 100).toFixed(1)}%</span>
        <button className="detail-close" onClick={onClose}>✕</button>
      </div>

      <p className="detail-question">この物体ですか？</p>

      {item.object_info?.thumbnail_key && (
        <p className="detail-thumbnail-note">
          候補物体 ID: {item.object_id?.slice(0, 8)}...
        </p>
      )}

      {confirmed === null && (
        <div className="detail-confirm-btns">
          <button
            className="confirm-btn confirm-yes"
            onClick={() => setConfirmed(true)}
          >
            はい
          </button>
          <button
            className="confirm-btn confirm-no"
            onClick={() => setConfirmed(false)}
          >
            いいえ
          </button>
        </div>
      )}

      {confirmed === true && (
        <p className="detail-confirmed">ありがとうございます。正解として記録しました。</p>
      )}
      {confirmed === false && (
        <p className="detail-confirmed">別の物体として記録しました。</p>
      )}
    </div>
  );
}

// ─── Red: 未知物体 → 登録導線 ─────────────────────────────────

function RedPanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="detail-panel detail-panel-red">
      <div className="detail-panel-header">
        <span className="verdict-badge verdict-red">未登録</span>
        <button className="detail-close" onClick={onClose}>✕</button>
      </div>
      <p className="detail-new-object">
        この物体はまだ登録されていません。
      </p>
      <button className="detail-register-btn" onClick={() => {
        // TODO: 登録フロー画面に遷移
        window.location.href = "/register";
      }}>
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
