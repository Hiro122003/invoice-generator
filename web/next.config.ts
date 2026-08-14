import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;

/*
 * 開発サーバーを webpack で動かしている（package.json の `next dev --webpack`）。
 *
 * Next 16 の既定は Turbopack だが、Docker のバインドマウント越しでは
 * ファイル変更を検知できず、編集のたびにコンテナ再起動が必要だった。
 * next.config の watchOptions.pollIntervalMs は Turbopack にも渡るはずだが、
 * Docker Desktop for Windows の環境では実際には発火しなかった。
 *
 * webpack + WATCHPACK_POLLING=true（docker-compose.yml で指定）では
 * 期待どおり自動反映される。ビルド（next build）は既定のままなので、
 * 本番の出力には影響しない。
 */
