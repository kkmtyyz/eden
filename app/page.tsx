"use client";

import { useAuthenticator } from "@aws-amplify/ui-react";

export default function Home() {
  const { user, signOut } = useAuthenticator();

  return (
    <main>
      <h1>Eden</h1>
      <p>物体認識・検索アプリ</p>

      {user && (
        <div style={{ marginTop: "1rem" }}>
          <p>ログイン中: {user.signInDetails?.loginId}</p>
          <button onClick={signOut} style={{ marginTop: "0.5rem" }}>
            サインアウト
          </button>
        </div>
      )}

      <section style={{ marginTop: "2rem", width: "100%", maxWidth: "400px" }}>
        <p style={{ textAlign: "center", color: "#666" }}>
          カメラ撮影 → 検索 → 結果表示 (実装予定)
        </p>
      </section>
    </main>
  );
}
