"use client";

import { useAuthenticator } from "@aws-amplify/ui-react";
import SearchFlow from "./components/search-flow";

export default function Home() {
  const { user, signOut } = useAuthenticator();

  return (
    <main className="app-main">
      {/* ヘッダー */}
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

      {/* メインコンテンツ: 検索フロー */}
      <SearchFlow />

      {/* フッターナビ */}
      <nav className="app-nav">
        <a href="/" className="nav-item nav-item-active">
          <span className="nav-icon">🔍</span>
          <span className="nav-label">検索</span>
        </a>
        <a href="/register" className="nav-item">
          <span className="nav-icon">📷</span>
          <span className="nav-label">登録</span>
        </a>
      </nav>
    </main>
  );
}
