"use client";

import { useRef, useState, useCallback } from "react";
import Camera, { CameraHandle } from "./camera";
import SearchResult from "./search-result";
import { uploadImage } from "../lib/upload";
import { searchImage, SearchResultItem } from "../lib/api";

type FlowState =
  | "camera"      // カメラプレビュー中
  | "uploading"   // アップロード中
  | "searching"   // 検索中
  | "results"     // 結果表示
  | "error";      // エラー

export default function SearchFlow() {
  const cameraRef = useRef<CameraHandle>(null);
  const [state, setState] = useState<FlowState>("camera");
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [capturedImageUrl, setCapturedImageUrl] = useState<string | null>(null);
  const [imageSize, setImageSize] = useState<{ w: number; h: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleCapture = useCallback(async () => {
    if (!cameraRef.current) return;

    const blob = cameraRef.current.captureFrame();
    if (!blob) {
      setError("フレームの取得に失敗しました");
      setState("error");
      return;
    }

    const objectUrl = URL.createObjectURL(blob);
    setCapturedImageUrl(objectUrl);

    const img = new Image();
    img.src = objectUrl;
    await new Promise((resolve) => {
      img.onload = resolve;
    });
    setImageSize({ w: img.naturalWidth, h: img.naturalHeight });

    try {
      setState("uploading");
      const { key } = await uploadImage(blob, `search_${Date.now()}.jpg`);

      setState("searching");
      const response = await searchImage(key);

      setResults(response.results);
      setState("results");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setState("error");
    }
  }, []);

  const handleRetry = useCallback(() => {
    if (capturedImageUrl) {
      URL.revokeObjectURL(capturedImageUrl);
    }
    setCapturedImageUrl(null);
    setImageSize(null);
    setResults([]);
    setError(null);
    setState("camera");
  }, [capturedImageUrl]);

  return (
    <div className="flex-1 flex flex-col">
      {/* カメラ表示 */}
      {state === "camera" && (
        <>
          <Camera
            ref={cameraRef}
            showGuide={true}
            guideText="物体を映してシャッターを押してください"
          />
          <div className="fixed bottom-[4.5rem] left-0 right-0 flex justify-center p-4 z-50">
            <button
              className="w-[72px] h-[72px] rounded-full border-4 border-white bg-transparent flex items-center justify-center active:scale-90 transition-transform"
              onClick={handleCapture}
              aria-label="撮影して検索"
            >
              <span className="w-[58px] h-[58px] rounded-full bg-white" />
            </button>
          </div>
        </>
      )}

      {/* ローディング */}
      {(state === "uploading" || state === "searching") && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 mt-12">
          {capturedImageUrl && (
            <img
              src={capturedImageUrl}
              alt="撮影画像"
              className="w-4/5 max-w-xs rounded-lg opacity-70"
            />
          )}
          <div className="w-8 h-8 border-[3px] border-white/20 border-t-white rounded-full animate-spin mt-6" />
          <p className="mt-3 text-sm text-white/70">
            {state === "uploading" ? "アップロード中..." : "検索中..."}
          </p>
        </div>
      )}

      {/* 結果表示 */}
      {state === "results" && capturedImageUrl && imageSize && (
        <div className="flex-1 flex flex-col mt-12 mb-16 overflow-y-auto">
          <SearchResult
            imageUrl={capturedImageUrl}
            imageWidth={imageSize.w}
            imageHeight={imageSize.h}
            results={results}
          />
          <button
            className="mx-auto my-4 px-6 py-2 bg-white/15 text-white border border-white/30 rounded-lg text-sm"
            onClick={handleRetry}
          >
            戻って撮り直す
          </button>
        </div>
      )}

      {/* エラー */}
      {state === "error" && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 mt-12">
          <div className="w-12 h-12 rounded-full bg-red-500 text-white flex items-center justify-center text-2xl font-bold">
            !
          </div>
          <p className="mt-4 text-sm text-white/70 text-center">{error}</p>
          <button
            className="mt-4 px-6 py-2 bg-white/15 text-white border border-white/30 rounded-lg text-sm"
            onClick={handleRetry}
          >
            やり直す
          </button>
        </div>
      )}
    </div>
  );
}
