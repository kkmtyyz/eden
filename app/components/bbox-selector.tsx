"use client";

import { useState } from "react";

interface BboxSelectorProps {
  imageUrl: string;
  imageWidth: number;
  imageHeight: number;
  boxes: number[][];
  defaultIndex: number | null;
  onConfirm: (box: number[]) => void;
}

/**
 * BBox 候補選択コンポーネント。
 * - 画像上に候補 BBox を表示
 * - デフォルト候補をハイライト
 * - タップで選択 → 確定ボタンで登録へ
 */
export default function BboxSelector({
  imageUrl,
  imageWidth,
  imageHeight,
  boxes,
  defaultIndex,
  onConfirm,
}: BboxSelectorProps) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(defaultIndex);

  console.log("[bbox-selector] 表示:", {
    boxCount: boxes.length,
    defaultIndex,
    selectedIdx,
  });

  return (
    <div className="flex flex-col items-center">
      {/* 画像 + BBox オーバーレイ */}
      <div className="relative w-full">
        <img src={imageUrl} alt="代表フレーム" className="w-full block" />
        {boxes.map((box, idx) => {
          const [x1, y1, x2, y2] = box;
          const isSelected = idx === selectedIdx;
          const style = {
            left: `${(x1 / imageWidth) * 100}%`,
            top: `${(y1 / imageHeight) * 100}%`,
            width: `${((x2 - x1) / imageWidth) * 100}%`,
            height: `${((y2 - y1) / imageHeight) * 100}%`,
          };
          return (
            <button
              key={idx}
              className={`absolute border-[2.5px] rounded p-0 cursor-pointer transition-all ${
                isSelected
                  ? "border-blue-400 bg-blue-400/20 shadow-[0_0_8px_rgba(96,165,250,0.6)]"
                  : "border-white/50 bg-transparent"
              }`}
              style={style}
              onClick={() => setSelectedIdx(idx)}
              aria-label={`候補 ${idx + 1}${isSelected ? " (選択中)" : ""}`}
            />
          );
        })}
      </div>

      {/* 選択状態 + 確定ボタン */}
      <div className="w-full px-4 py-4">
        {boxes.length === 0 ? (
          <p className="text-center text-sm text-white/60">
            物体が検出されませんでした。撮り直してください。
          </p>
        ) : (
          <>
            <p className="text-center text-xs text-white/50 mb-3">
              {selectedIdx !== null
                ? `候補 ${selectedIdx + 1} / ${boxes.length} を選択中`
                : "枠をタップして選択してください"}
            </p>
            <button
              className="w-full py-3 bg-blue-600 text-white rounded-lg text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed"
              onClick={() => {
                if (selectedIdx !== null) {
                  onConfirm(boxes[selectedIdx]);
                }
              }}
              disabled={selectedIdx === null}
            >
              この物体を登録する
            </button>
          </>
        )}
      </div>
    </div>
  );
}
