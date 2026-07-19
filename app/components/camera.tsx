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

    // toBlob は非同期だが、toDataURL → Blob 変換で同期的に返す
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
      <div className="camera-error">
        <p>カメラを起動できません</p>
        <p className="camera-error-detail">{error}</p>
        <button onClick={startCamera} className="camera-retry-btn">
          再試行
        </button>
      </div>
    );
  }

  return (
    <div className="camera-container">
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="camera-video"
      />
      {/* オフスクリーン canvas (キャプチャ用) */}
      <canvas ref={canvasRef} style={{ display: "none" }} />

      {/* ガイド枠オーバーレイ */}
      {showGuide && isActive && (
        <div className="camera-guide-overlay">
          <div className="camera-guide-frame" />
          <p className="camera-guide-text">{guideText}</p>
        </div>
      )}
    </div>
  );
});

export default Camera;
