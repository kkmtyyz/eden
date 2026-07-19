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

    // キャプチャした画像をプレビュー用に保持
    const objectUrl = URL.createObjectURL(blob);
    setCapturedImageUrl(objectUrl);

    // 画像サイズ取得
    const img = new Image();
    img.src = objectUrl;
    await new Promise((resolve) => {
      img.onload = resolve;
    });
    setImageSize({ w: img.naturalWidth, h: img.naturalHeight });

    try {
      // 1. アップロード
      setState("uploading");
      const { key } = await uploadImage(blob, `search_${Date.now()}.jpg`);

      // 2. 検索リクエスト
      setState("searching");
      const response = await searchImage(key);

      // 3. 結果表示
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
    <div className="search-flow">
      {/* カメラ表示 (camera/uploading/searching 時) */}
      {state === "camera" && (
        <>
          <Camera
            ref={cameraRef}
            showGuide={true}
            guideText="物体を映してシャッターを押してください"
          />
          <div className="search-flow-actions">
            <button
              className="shutter-btn"
              onClick={handleCapture}
              aria-label="撮影して検索"
            >
              <span className="shutter-btn-inner" />
            </button>
          </div>
        </>
      )}

      {/* ローディング */}
      {(state === "uploading" || state === "searching") && (
        <div className="search-flow-loading">
          {capturedImageUrl && (
            <img
              src={capturedImageUrl}
              alt="撮影画像"
              className="search-flow-preview"
            />
          )}
          <div className="search-flow-spinner" />
          <p className="search-flow-status">
            {state === "uploading" ? "アップロード中..." : "検索中..."}
          </p>
        </div>
      )}

      {/* 結果表示 */}
      {state === "results" && capturedImageUrl && imageSize && (
        <div className="search-flow-results">
          <SearchResult
            imageUrl={capturedImageUrl}
            imageWidth={imageSize.w}
            imageHeight={imageSize.h}
            results={results}
          />
          <button className="search-flow-back-btn" onClick={handleRetry}>
            戻って撮り直す
          </button>
        </div>
      )}

      {/* エラー */}
      {state === "error" && (
        <div className="search-flow-error">
          <p className="error-icon">!</p>
          <p className="error-message">{error}</p>
          <button className="search-flow-back-btn" onClick={handleRetry}>
            やり直す
          </button>
        </div>
      )}
    </div>
  );
}
