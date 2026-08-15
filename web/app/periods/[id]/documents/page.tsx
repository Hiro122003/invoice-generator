"use client";

/**
 * P-10 発行済み書類。
 *
 * 過去に出力したPDFを版数つきで一覧・再ダウンロードする（F-11の一部）。
 * 「先方に何を送ったか」の正はDBの数値ではなくこのPDFそのもの
 * （docs/design.md）。訂正で版数が上がっても、古い版のPDFはそのまま
 * 残り続ける。
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  type IssuedDocumentRow,
  documentDownloadUrl,
  fetchDocuments,
  formatDateTime,
  formatDocType,
} from "@/lib/api";

function formatSize(bytes: number | null): string {
  if (bytes === null) return "—";
  return `${Math.ceil(bytes / 1024).toLocaleString("ja-JP")} KB`;
}

export default function DocumentsPage() {
  const params = useParams<{ id: string }>();
  const periodId = Number(params.id);

  const [docs, setDocs] = useState<IssuedDocumentRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchDocuments(periodId)
      .then(setDocs)
      .catch((e) => setError(e instanceof Error ? e.message : "取得に失敗しました"));
  }, [periodId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="wide">
      <header className="page-head">
        <div>
          <h1>発行済み書類</h1>
          <p className="lede">
            過去に出力したPDFを版数つきで一覧します。訂正で版数が上がっても
            以前のPDFはそのまま残り、いつでも再ダウンロードできます。
          </p>
        </div>
        <div className="actions">
          <Link href={`/periods/${periodId}/export`} className="btn primary">
            PDF出力へ
          </Link>
          <Link href={`/periods/${periodId}/invoices`} className="btn">
            請求書へ
          </Link>
        </div>
      </header>

      {error && <p className="err">{error}</p>}

      {docs && (
        <div className="summarybar">
          <span>
            <strong>{docs.length.toLocaleString("ja-JP")}</strong> 件
          </span>
        </div>
      )}

      {docs && docs.length === 0 && (
        <div className="empty">
          <p>発行済みのPDFがありません。</p>
          <p className="sub">PDF出力画面から出力すると、ここに記録されます。</p>
        </div>
      )}

      {docs && docs.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>種類</th>
                <th className="num">版数</th>
                <th>ファイル名</th>
                <th className="num">サイズ</th>
                <th>発行者</th>
                <th>発行日時</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>
                    <span className={`tag ${d.doc_type === "BUNDLE_ZIP" ? "counter" : ""}`}>
                      {formatDocType(d.doc_type)}
                    </span>
                  </td>
                  <td className="num">第{d.revision}版</td>
                  <td className="mono">{d.file_name}</td>
                  <td className="num">{formatSize(d.byte_size)}</td>
                  <td>{d.issued_by_name}</td>
                  <td className="muted">{formatDateTime(d.issued_at)}</td>
                  <td>
                    <a href={documentDownloadUrl(d.id)} className="btn ghost small">
                      ダウンロード
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
