import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AmplifyProvider } from "./amplify-provider";

export const metadata: Metadata = {
  title: "Eden - 物体認識・検索",
  description: "物体を撮影して検索・登録するアプリ",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>
        <AmplifyProvider>{children}</AmplifyProvider>
      </body>
    </html>
  );
}
