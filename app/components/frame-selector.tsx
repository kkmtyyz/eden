"use client";

import { useState } from "react";
import { CapturedFrame } from "../lib/frame-capture";

interface FrameSelectorProps {
  frames: CapturedFrame[];
  onConfirm: (representativeIndex: number) => void;
}

/**
 * プルーニング後のフレーム一覧を表示し、
 * ユーザーにサムネ用の代表画像を1枚選ばせるコンポーネント。
 */
export default function FrameSelector({ frames, onConfirm }: FrameSelectorProps) {
  const [selectedIdx, setSelectedIdx] = useState(0); // デフォルト: 最初の1枚
  const [objectUrls] = useState(() => frames.map((f) => URL.createObjectURL(f.blob)));

  console.log("[frame-selector] 表示:", { frameCount: frames.length, selectedIdx });

  return (
    <div className="flex flex-col items-center px-4 mt-12 mb-16">
      {/* 選択中の画像（大きく表示） */}
      <div className="w-full max-w-sm mb-4">
        <img
          src={objectUrls[selectedIdx]}
          alt={`フレーム ${selectedIdx + 1}`}
          className="w-full rounded-lg border-2 border-blue-400"
        />
        <p className="text-center text-xs text-white/50 mt-2">
          {selectedIdx + 1} / {frames.length} 枚目を代表画像に選択中
        </p>
      </div>

      {/* サムネイル一覧 */}
      <div className="w-full overflow-x-auto pb-2">
        <div className="flex gap-2 min-w-min">
          {objectUrls.map((url, idx) => (
            <button
              key={idx}
              className={`flex-shrink-0 w-16 h-16 rounded-md overflow-hidden border-2 transition-all ${
                idx === selectedIdx
                  ? "border-blue-400 scale-110 shadow-[0_0_8px_rgba(96,165,250,0.5)]"
                  : "border-white/20 opacity-60"
              }`}
              onClick={() => setSelectedIdx(idx)}
              aria-label={`フレーム ${idx + 1}${idx === selectedIdx ? " (選択中)" : ""}`}
            >
              <img src={url} alt={`フレーム ${idx + 1}`} className="w-full h-full object-cover" />
            </button>
          ))}
        </div>
      </div>

      {/* 確定ボタン */}
      <button
        className="mt-4 w-full max-w-sm py-3 bg-blue-600 text-white rounded-lg text-sm font-semibold"
        onClick={() => onConfirm(selectedIdx)}
      >
        この画像を代表に選んで次へ
      </button>

      <p className="mt-2 text-xs text-white/40 text-center">
        代表画像は登録物体のサムネイルになります。全{frames.length}枚が登録に使用されます。
      </p>
    </div>
  );
}
