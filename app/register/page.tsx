"use client";

import { useAuthenticator } from "@aws-amplify/ui-react";

/**
 * 物体登録画面（プレースホルダー）。
 * 次のステップで実装:
 * - カメラ起動 → 100ms キャプチャ → プルーニング
 * - presigned PUT でアップロード
 * - BBox候補取得 → ユーザー選択
 * - 登録リクエスト → ジョブ状態ポーリング
 */
export default function RegisterPage() {
  const { user, signOut } = useAuthenticator();

  return (
    <main className="app-main">
      <header className="app-header">
        <h1 className="app-title">Eden</h1>
        <div className="app-header-right">
          {user && (
            <button onClick={signOut} className="signout-btn" aria-label="サインアウト">
              ログアウト
            </button>
          )}
        </div>
      </header>

      <div className="register-placeholder">
        <h2>物体登録</h2>
        <p>
          物体の周り・上をゆっくり動かして撮影します。
        </p>
        <p className="placeholder-note">
          (登録フロー実装予定)
        </p>
      </div>

      <nav className="app-nav">
        <a href="/" className="nav-item">
          <span className="nav-icon">🔍</span>
          <span className="nav-label">検索</span>
        </a>
        <a href="/register" className="nav-item nav-item-active">
          <span className="nav-icon">📷</span>
          <span className="nav-label">登録</span>
        </a>
      </nav>
    </main>
  );
}
