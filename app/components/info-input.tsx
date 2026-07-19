"use client";

import { useState } from "react";

interface InfoInputProps {
  imageUrl: string;
  onConfirm: (text: string) => void;
}

/**
 * 登録時の初期物体情報入力コンポーネント。
 * BBox選択後に表示し、物体の名前や説明を入力させる。
 */
export default function InfoInput({ imageUrl, onConfirm }: InfoInputProps) {
  const [text, setText] = useState("");

  console.log("[info-input] 表示");

  return (
    <div className="flex-1 flex flex-col items-center px-4 mt-12 mb-16">
      {/* 選択した物体のプレビュー */}
      <div className="w-full max-w-sm mb-4">
        <img
          src={imageUrl}
          alt="登録物体"
          className="w-full rounded-lg opacity-80"
        />
      </div>

      {/* 入力フォーム */}
      <div className="w-full max-w-sm">
        <label className="block text-sm text-white/70 mb-2">
          この物体の情報を入力してください
        </label>
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="例: AWSマグカップ、田中さんの傘、会議室Aのリモコン"
          className="w-full px-4 py-3 bg-white/10 border border-white/20 rounded-lg text-white text-sm placeholder:text-white/40 focus:outline-none focus:border-blue-400"
          autoFocus
        />
        <p className="mt-2 text-xs text-white/40">
          検索でヒットしたときに表示される情報です。後から追加もできます。
        </p>
      </div>

      {/* 確定ボタン */}
      <button
        className="mt-6 w-full max-w-sm py-3 bg-blue-600 text-white rounded-lg text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed"
        onClick={() => onConfirm(text.trim())}
        disabled={!text.trim()}
      >
        この情報で登録する
      </button>
    </div>
  );
}
