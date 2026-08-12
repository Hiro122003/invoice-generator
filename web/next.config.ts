import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Docker のバインドマウント上ではファイル変更イベントが拾えないため
  // ポーリングで検出する（docker-compose で WATCHPACK_POLLING=true）
  reactStrictMode: true,
};

export default nextConfig;
