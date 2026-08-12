import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "請求書生成システム",
  description: "月次の請求書・請求明細書を作成する",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
