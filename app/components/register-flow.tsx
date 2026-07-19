"use client";

import { useRef, useState, useCallback } from "react";
import Camera, { CameraHandle } from "./camera";
import FrameSelector from "./frame-selector";
import InfoInput from "./info-input";
import BboxSelector from "./bbox-selector";
import JobProgress from "./job-progress";
import {
  createFrameCaptureSession,
  CapturedFrame,
  FrameCaptureSession,
} from "../lib/frame-capture";
import { uploadImages } from "../lib/upload";
import {
  getBboxCandidates,
  registerObject,
  BboxCandidatesResponse,
} from "../lib/api";

/**
 * 登録フローの状態
 */
type FlowState =
  | "camera"        // カメラプレビュー（キャプチャ待機）
  | "capturing"     // マルチフレームキャプチャ中
  | "pruning"       // プルーニング中
  | "frame-select"  // サムネ用代表画像選択
  | "uploading"     // S3 アップロード中
  | "bbox"          // BBox 候補選択
  | "info-input"    // 物体情報入力
  | "registering"   // 登録リクエスト中
  | "polling"       // ジョブ進捗ポーリング
  | "done"          // 完了
  | "error";        // エラー

export default function RegisterFlow() {
  const cameraRef = useRef<CameraHandle>(null);
  const sessionRef = useRef<FrameCaptureSession | null>(null);

  const [state, setState] = useState<FlowState>("camera");
  const [frameCount, setFrameCount] = useState(0);
  const [prunedFrames, setPrunedFrames] = useState<CapturedFrame[]>([]);
  const [representativeIdx, setRepresentativeIdx] = useState(0);
  const [uploadedKeys, setUploadedKeys] = useState<string[]>([]);
  const [uploadProgress, setUploadProgress] = useState({ done: 0, total: 0 });
  const [bboxData, setBboxData] = useState<BboxCandidatesResponse | null>(null);
  const [representativeUrl, setRepresentativeUrl] = useState<string | null>(null);
  const [selectedBox, setSelectedBox] = useState<number[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ─── Step 1: キャプチャ開始 ─────────────────────────────────
  const handleStartCapture = useCallback(() => {
    if (!cameraRef.current) return;
    console.log("[register-flow] === キャプチャ開始 ===");

    const session = createFrameCaptureSession(() =>
      cameraRef.current?.captureFrame() ?? null
    );
    sessionRef.current = session;
    session.start();
    setState("capturing");

    // フレーム数のリアルタイム更新
    const countInterval = setInterval(() => {
      if (sessionRef.current) {
        setFrameCount(sessionRef.current.rawCount);
      }
    }, 200);

    (sessionRef.current as unknown as { _countInterval: ReturnType<typeof setInterval> })._countInterval = countInterval;
  }, []);

  // ─── Step 2: キャプチャ停止 → プルーニング → フレーム選択へ ──
  const handleStopCapture = useCallback(async () => {
    const session = sessionRef.current;
    if (!session) return;

    const interval = (session as unknown as { _countInterval: ReturnType<typeof setInterval> })._countInterval;
    if (interval) clearInterval(interval);

    console.log("[register-flow] キャプチャ停止、プルーニング開始");
    setState("pruning");

    try {
      const frames = await session.stop();
      console.log("[register-flow] プルーニング完了:", { rawCount: frameCount, prunedCount: frames.length });
      setPrunedFrames(frames);

      if (frames.length === 0) {
        throw new Error("有効なフレームがありません。もう一度撮影してください。");
      }

      // フレーム選択ステップへ
      setState("frame-select");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error("[register-flow] エラー:", msg);
      setError(msg);
      setState("error");
    }
  }, [frameCount]);

  // ─── Step 3: 代表フレーム選択確定 → アップロード → BBox ────
  const handleFrameSelect = useCallback(
    async (selectedIdx: number) => {
      setRepresentativeIdx(selectedIdx);
      console.log("[register-flow] 代表画像選択:", { selectedIdx, totalFrames: prunedFrames.length });

      try {
        // アップロード
        console.log("[register-flow] S3 アップロード開始:", { count: prunedFrames.length });
        setState("uploading");
        setUploadProgress({ done: 0, total: prunedFrames.length });

        const blobs = prunedFrames.map((f) => f.blob);
        const results = await uploadImages(blobs, (done, total) => {
          setUploadProgress({ done, total });
        });

        const keys = results.map((r) => r.key);
        setUploadedKeys(keys);
        console.log("[register-flow] アップロード完了:", { keys, representativeKey: keys[selectedIdx] });

        // 代表フレームで BBox 候補取得
        const representativeKey = keys[selectedIdx];
        console.log("[register-flow] BBox 候補取得中:", { representativeKey });
        const bboxRes = await getBboxCandidates(representativeKey);
        console.log("[register-flow] BBox 候補:", bboxRes);
        setBboxData(bboxRes);

        // 代表フレームのプレビュー用 URL
        const previewUrl = URL.createObjectURL(prunedFrames[selectedIdx].blob);
        setRepresentativeUrl(previewUrl);

        setState("bbox");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error("[register-flow] エラー:", msg);
        setError(msg);
        setState("error");
      }
    },
    [prunedFrames]
  );

  // ─── Step 4: BBox 選択確定 → 情報入力へ ─────────────────────
  const handleBboxConfirm = useCallback(
    async (box: number[]) => {
      setSelectedBox(box);
      console.log("[register-flow] BBox 選択確定:", box);
      setState("info-input");
    },
    []
  );

  // ─── Step 5: 情報入力確定 → 登録 ───────────────────────────
  const handleInfoConfirm = useCallback(
    async (infoText: string) => {
      console.log("[register-flow] 情報入力確定:", infoText);
      console.log("[register-flow] 登録リクエスト送信中... (全 %d 枚)", uploadedKeys.length);
      setState("registering");

      try {
        const representativeKey = uploadedKeys[representativeIdx];
        const res = await registerObject(uploadedKeys, representativeKey, selectedBox!, undefined, infoText);
        console.log("[register-flow] 登録ジョブ投入成功:", res);
        setJobId(res.job_id);
        setState("polling");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error("[register-flow] 登録エラー:", msg);
        setError(msg);
        setState("error");
      }
    },
    [uploadedKeys, representativeIdx, selectedBox]
  );

  // ─── Step 5: ジョブ完了 ─────────────────────────────────────
  const handleJobComplete = useCallback(() => {
    console.log("[register-flow] 登録完了!");
    setState("done");
  }, []);

  const handleJobError = useCallback((msg: string) => {
    console.error("[register-flow] ジョブ失敗:", msg);
    setError(msg);
    setState("error");
  }, []);

  // ─── リセット ───────────────────────────────────────────────
  const handleReset = useCallback(() => {
    console.log("[register-flow] リセット");
    if (representativeUrl) URL.revokeObjectURL(representativeUrl);
    setState("camera");
    setFrameCount(0);
    setPrunedFrames([]);
    setRepresentativeIdx(0);
    setUploadedKeys([]);
    setUploadProgress({ done: 0, total: 0 });
    setBboxData(null);
    setRepresentativeUrl(null);
    setJobId(null);
    setError(null);
  }, [representativeUrl]);

  console.log("[register-flow] 現在の状態:", state);

  return (
    <div className="flex-1 flex flex-col">
      {/* ─── カメラ + キャプチャ待機/実行中 ─── */}
      {(state === "camera" || state === "capturing") && (
        <>
          <Camera
            ref={cameraRef}
            showGuide={true}
            guideText="物体の周りをゆっくり動かしてください"
          />

          {/* キャプチャ中インジケーター */}
          {state === "capturing" && (
            <div className="fixed top-16 left-0 right-0 flex justify-center z-50">
              <div className="bg-red-500/90 backdrop-blur-sm px-4 py-2 rounded-full flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-white animate-pulse" />
                <span className="text-white text-sm font-medium">
                  撮影中: {frameCount} フレーム
                </span>
              </div>
            </div>
          )}

          {/* 操作ボタン */}
          <div className="fixed bottom-[4.5rem] left-0 right-0 flex justify-center p-4 z-50">
            {state === "camera" ? (
              <button
                className="px-8 py-3 bg-red-500 text-white rounded-full text-sm font-semibold active:scale-95 transition-transform"
                onClick={handleStartCapture}
              >
                撮影開始
              </button>
            ) : (
              <button
                className="px-8 py-3 bg-white text-black rounded-full text-sm font-semibold active:scale-95 transition-transform"
                onClick={handleStopCapture}
              >
                撮影完了 → 登録へ
              </button>
            )}
          </div>
        </>
      )}

      {/* ─── プルーニング中 ─── */}
      {state === "pruning" && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 mt-12">
          <div className="w-8 h-8 border-[3px] border-white/20 border-t-white rounded-full animate-spin" />
          <p className="mt-4 text-sm text-white/70">
            フレーム選別中... ({frameCount} → 最大10枚)
          </p>
        </div>
      )}

      {/* ─── フレーム選択 (サムネ用代表画像) ─── */}
      {state === "frame-select" && prunedFrames.length > 0 && (
        <FrameSelector frames={prunedFrames} onConfirm={handleFrameSelect} />
      )}

      {/* ─── アップロード中 ─── */}
      {state === "uploading" && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 mt-12">
          <div className="w-8 h-8 border-[3px] border-white/20 border-t-white rounded-full animate-spin" />
          <p className="mt-4 text-sm text-white/70">
            アップロード中... ({uploadProgress.done}/{uploadProgress.total})
          </p>
          <div className="w-3/4 max-w-xs h-2 bg-white/10 rounded-full mt-3 overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-300"
              style={{
                width: uploadProgress.total > 0
                  ? `${(uploadProgress.done / uploadProgress.total) * 100}%`
                  : "0%",
              }}
            />
          </div>
        </div>
      )}

      {/* ─── BBox 候補選択 ─── */}
      {state === "bbox" && bboxData && representativeUrl && (
        <div className="flex-1 flex flex-col mt-12 mb-16 overflow-y-auto">
          <p className="text-center text-sm text-white/70 py-3">
            登録する物体をタップしてください
          </p>
          <BboxSelector
            imageUrl={representativeUrl}
            imageWidth={bboxData.image_size[0]}
            imageHeight={bboxData.image_size[1]}
            boxes={bboxData.boxes}
            defaultIndex={bboxData.default_index}
            onConfirm={handleBboxConfirm}
          />
        </div>
      )}

      {/* ─── 物体情報入力 ─── */}
      {state === "info-input" && representativeUrl && (
        <InfoInput imageUrl={representativeUrl} onConfirm={handleInfoConfirm} />
      )}

      {/* ─── 登録リクエスト送信中 ─── */}
      {state === "registering" && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 mt-12">
          <div className="w-8 h-8 border-[3px] border-white/20 border-t-white rounded-full animate-spin" />
          <p className="mt-4 text-sm text-white/70">登録処理を開始しています...</p>
        </div>
      )}

      {/* ─── ジョブポーリング ─── */}
      {state === "polling" && jobId && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 mt-12">
          <JobProgress
            jobId={jobId}
            onComplete={handleJobComplete}
            onError={handleJobError}
          />
        </div>
      )}

      {/* ─── 完了 ─── */}
      {state === "done" && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 mt-12">
          <div className="w-16 h-16 rounded-full bg-green-500 flex items-center justify-center text-3xl">
            ✓
          </div>
          <p className="mt-4 text-lg font-semibold">登録完了!</p>
          <p className="mt-2 text-sm text-white/60">
            物体が正常に登録されました。検索で見つかるようになります。
          </p>
          <div className="flex gap-3 mt-6">
            <button
              className="px-6 py-2 bg-white/15 text-white border border-white/30 rounded-lg text-sm"
              onClick={handleReset}
            >
              続けて登録
            </button>
            <a
              href="/"
              className="px-6 py-2 bg-blue-600 text-white rounded-lg text-sm no-underline"
            >
              検索画面へ
            </a>
          </div>
        </div>
      )}

      {/* ─── エラー ─── */}
      {state === "error" && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 mt-12">
          <div className="w-12 h-12 rounded-full bg-red-500 text-white flex items-center justify-center text-2xl font-bold">
            !
          </div>
          <p className="mt-4 text-sm text-white/70 text-center">{error}</p>
          <button
            className="mt-4 px-6 py-2 bg-white/15 text-white border border-white/30 rounded-lg text-sm"
            onClick={handleReset}
          >
            やり直す
          </button>
        </div>
      )}
    </div>
  );
}
