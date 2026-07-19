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
    <main className="min-h-dvh flex flex-col bg-black text-white">
      {/* ヘッダー */}
      <header className="flex items-center justify-between px-4 py-3 bg-black/80 backdrop-blur-md fixed top-0 left-0 right-0 z-[100]">
        <h1 className="text-xl font-bold">Eden</h1>
        {user && (
          <button
            onClick={signOut}
            className="px-3 py-1 text-xs border border-white/30 rounded text-white bg-transparent"
            aria-label="サインアウト"
          >
            ログアウト
          </button>
        )}
      </header>

      {/* コンテンツ */}
      <div className="flex-1 flex flex-col items-center justify-center p-8 mt-12 text-center">
        <h2 className="text-2xl font-semibold mb-4">物体登録</h2>
        <p className="text-sm text-white/70">
          物体の周り・上をゆっくり動かして撮影します。
        </p>
        <p className="mt-4 text-xs text-white/40">(登録フロー実装予定)</p>
      </div>

      {/* フッターナビ */}
      <nav className="flex fixed bottom-0 left-0 right-0 bg-black/90 backdrop-blur-md border-t border-white/10 z-[100] pb-[env(safe-area-inset-bottom)]">
        <a
          href="/"
          className="flex-1 flex flex-col items-center py-2 text-white/50 text-[0.7rem] no-underline"
        >
          <span className="text-xl mb-0.5">🔍</span>
          <span>検索</span>
        </a>
        <a
          href="/register"
          className="flex-1 flex flex-col items-center py-2 text-white text-[0.7rem] no-underline"
        >
          <span className="text-xl mb-0.5">📷</span>
          <span>登録</span>
        </a>
      </nav>
    </main>
  );
}
