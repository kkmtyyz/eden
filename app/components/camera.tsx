"use client";

import {
  useRef,
  useEffect,
  useState,
  useCallback,
  forwardRef,
  useImperativeHandle,
} from "react";

export interface CameraHandle {
  /** 現在のフレームを JPEG Blob として取得 */
  captureFrame: () => Blob | null;
  /** カメラ停止 */
  stop: () => void;
}

interface CameraProps {
  /** カメラ起動時コールバック */
  onReady?: () => void;
  /** エラー時コールバック */
  onError?: (err: Error) => void;
  /** ガイド枠を表示するか (default: true) */
  showGuide?: boolean;
  /** ガイドテキスト */
  guideText?: string;
}

/**
 * スマホ向けカメラコンポーネント。
 * - リアカメラ(environment)をデフォルト使用
 * - ガイド枠で物体の位置合わせをサポート
 * - ref 経由で captureFrame() を呼べる
 */
const Camera = forwardRef<CameraHandle, CameraProps>(function Camera(
  { onReady, onError, showGuide = true, guideText = "物体を枠内に収めてください" },
  ref
) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [isActive, setIsActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startCamera = useCallback(async () => {
    try {
      const constraints: MediaStreamConstraints = {
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      };

      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      streamRef.current = stream;

      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
        setIsActive(true);
        setError(null);
        onReady?.();
      }
    } catch (err) {
      const e = err instanceof Error ? err : new Error(String(err));
      setError(e.message);
      onError?.(e);
    }
  }, [onReady, onError]);

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    setIsActive(false);
  }, []);

  const captureFrame = useCallback((): Blob | null => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !isActive) return null;

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;

    ctx.drawImage(video, 0, 0);

    const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
    const binary = atob(dataUrl.split(",")[1]);
    const arr = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      arr[i] = binary.charCodeAt(i);
    }
    return new Blob([arr], { type: "image/jpeg" });
  }, [isActive]);

  useImperativeHandle(ref, () => ({
    captureFrame,
    stop: stopCamera,
  }));

  useEffect(() => {
    startCamera();
    return () => {
      stopCamera();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-center min-h-[60dvh]">
        <p className="text-white text-base">カメラを起動できません</p>
        <p className="text-xs text-white/50 mt-2">{error}</p>
        <button
          onClick={startCamera}
          className="mt-4 px-6 py-2 bg-blue-600 text-white rounded-lg text-sm"
        >
          再試行
        </button>
      </div>
    );
  }

  return (
    <div className="relative w-full flex-1 flex items-center justify-center bg-black mt-12 mb-16 overflow-hidden">
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="w-full h-full object-cover"
      />
      {/* オフスクリーン canvas (キャプチャ用) */}
      <canvas ref={canvasRef} className="hidden" />

      {/* ガイド枠オーバーレイ */}
      {showGuide && isActive && (
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <div className="w-[70%] max-w-[280px] aspect-square border-2 border-white/60 rounded-xl" />
          <p className="mt-4 text-xs text-white/80 drop-shadow-md">
            {guideText}
          </p>
        </div>
      )}
    </div>
  );
});

export default Camera;
