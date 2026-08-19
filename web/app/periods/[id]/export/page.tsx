"use client";

/**
 * P-08 PDF出力。
 *
 * 請求書・請求明細書をPDF化する（F-09）。会社（税率）ごとのPDFと、
 * まとめてダウンロードできるZIPを1回の操作で作る。出力するたびに
 * issued_document へ記録され、削除されない（過去の版もそのまま残る）。
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  type ExportResult,
  type IssuedDocumentRow,
  documentDownloadUrl,
  exportPeriod,
  fetchDocuments,
  formatDateTime,
  formatDocType,
} from "@/lib/api";

function formatSize(bytes: number | null): string {
  if (bytes === null) return "—";
  return `${Math.ceil(bytes / 1024).toLocaleString("ja-JP")} KB`;
}

export default function ExportPage() {
  const params = useParams<{ id: string }>();
  const periodId = Number(params.id);

  const [recent, setRecent] = useState<IssuedDocumentRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [justExported, setJustExported] = useState<ExportResult | null>(null);

  const load = useCallback(() => {
    fetchDocuments(periodId)
      .then(setRecent)
      .catch((e) => setError(e instanceof Error ? e.message : "取得に失敗しました"));
  }, [periodId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleExport = async () => {
    setExporting(true);
    setError(null);
    setJustExported(null);
    try {
      const result = await exportPeriod(periodId);
      setJustExported(result);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "PDF出力に失敗しました");
    } finally {
      setExporting(false);
    }
  };

  return (
    <main className="wide">
      <header className="page-head">
        <div>
          <h1>PDF出力</h1>
          <p className="lede">
            請求書・請求明細書をPDFで出力します。税率ごとのPDFと、まとめて
            ダウンロードできるZIPを一度に作成します。
          </p>
        </div>
        <div className="actions">
          <button className="btn primary" onClick={handleExport} disabled={exporting}>
            {exporting ? "出力しています…" : "PDFを出力する"}
          </button>
          <Link href={`/periods/${periodId}/invoices`} className="btn">
            請求書へ
          </Link>
          <Link href={`/periods/${periodId}/validate`} className="btn">
            発行前チェックへ
          </Link>
          <Link href={`/periods/${periodId}/documents`} className="btn">
            発行済み書類一覧へ
          </Link>
        </div>
      </header>

      {error && <p className="err">{error}</p>}

      {justExported && (
        <div className="preview-panel">
          <div className="preview-head">
            <span>
              <strong className="preview-count">{justExported.files.length}</strong>{" "}
              件のファイルを出力しました。下の一覧からダウンロードできます。
            </span>
          </div>
        </div>
      )}

      {recent && recent.length === 0 && (
        <div className="empty">
          <p>まだPDFを出力していません。</p>
          <p className="sub">上の「PDFを出力する」ボタンを押すと作成されます。</p>
        </div>
      )}

      {recent && recent.length > 0 && (
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
              {recent.map((d) => (
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
