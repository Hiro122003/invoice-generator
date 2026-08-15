"use client";

/**
 * P-06 請求書。
 *
 * 税率ごとに1通（10%・8%）。まだ生成されていなければここに
 * 「生成する」ボタンを置く（F-04/F-06のトリガー）。
 * 各請求書の下に明細書を一覧し、行クリックで対応する明細書（P-05）へ
 * 遷移する（VBAのダブルクリック機能の正統進化）。
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  type ContractFilters,
  type ContractListResponse,
  type InvoiceRow,
  type StatementSummaryRow,
  fetchContracts,
  fetchInvoiceStatements,
  fetchInvoices,
  formatYen,
  generatePeriod,
} from "@/lib/api";

const TAX_LABEL: Record<InvoiceRow["tax_category"], string> = {
  STANDARD: "10%",
  REDUCED: "8%",
};

// リスト表（P-03）で「明細不要」に絞り込むためのフィルタ。他の条件は
// 表示の絞り込みにしか使わないため生成には関係なく、ここでは使わない。
const SKIP_ONLY_FILTERS: ContractFilters = {
  client: "",
  site: "",
  contract_no: "",
  tax: "",
  group: "",
  skip_statement: "true",
  min_amount: "",
  max_amount: "",
};

function SkipStatementPreview({ periodId }: { periodId: number }) {
  const [data, setData] = useState<ContractListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchContracts(periodId, SKIP_ONLY_FILTERS)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "取得に失敗しました");
      });
    return () => {
      cancelled = true;
    };
  }, [periodId]);

  if (error || !data || data.summary.count === 0) return null;

  return (
    <div className="preview-panel">
      <div className="preview-head">
        <span>
          明細不要により <strong className="preview-count">{data.summary.count}</strong> 件
          （税抜 {formatYen(data.summary.total_ex_tax)} 円）が発行対象から除外されます。
        </span>
        <div className="actions">
          <button className="btn ghost small" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "閉じる" : "内訳を見る"}
          </button>
          <Link href={`/periods/${periodId}/contracts?skip_statement=true`} className="btn ghost small">
            リスト表で調整する
          </Link>
        </div>
      </div>
      {expanded && (
        <ul>
          {data.items.map((c) => (
            <li key={c.id}>
              <span className="mono">{c.contract_no}</span> {c.client_name} / {c.site_name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function InvoiceCard({ invoice }: { invoice: InvoiceRow }) {
  const [statements, setStatements] = useState<StatementSummaryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchInvoiceStatements(invoice.id)
      .then((d) => {
        if (!cancelled) setStatements(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "取得に失敗しました");
      });
    return () => {
      cancelled = true;
    };
  }, [invoice.id]);

  return (
    <section className="invoice-card">
      <header className="invoice-head">
        <div>
          <span className={`tax-badge ${invoice.tax_category === "REDUCED" ? "reduced" : ""}`}>
            {TAX_LABEL[invoice.tax_category]}
          </span>
          <span className="customer">{invoice.customer_name} 御中</span>
        </div>
        <dl className="invoice-totals">
          <div>
            <dt>税抜</dt>
            <dd>{formatYen(invoice.total_ex_tax)}</dd>
          </div>
          <div>
            <dt>消費税</dt>
            <dd>{formatYen(invoice.tax_amount)}</dd>
          </div>
          <div className="grand">
            <dt>合計</dt>
            <dd>{formatYen(invoice.total_amount)}</dd>
          </div>
        </dl>
      </header>

      {error && <p className="err small">{error}</p>}
      {!error && !statements && <p className="muted small">読み込んでいます…</p>}

      {statements && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>契約番号</th>
                <th>現場</th>
                <th>区分</th>
                <th className="num">税抜</th>
                <th className="num">合計</th>
              </tr>
            </thead>
            <tbody>
              {statements.map((s) => (
                <tr key={s.id}>
                  <td className="mono">
                    <Link href={`/statements/${s.id}`} className="celllink">
                      {s.contract_no}
                    </Link>
                  </td>
                  <td>{s.site_name}</td>
                  <td>
                    <span className={`tag ${s.billing_group === "COUNTER" ? "counter" : ""}`}>
                      {s.billing_group === "COUNTER" ? "カウンタ" : "備品"}
                    </span>
                  </td>
                  <td className="num">{formatYen(s.total_ex_tax)}</td>
                  <td className="num strong">{formatYen(s.total_amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function InvoicesPage() {
  const params = useParams<{ id: string }>();
  const periodId = Number(params.id);

  const [invoices, setInvoices] = useState<InvoiceRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  const load = useCallback(() => {
    fetchInvoices(periodId)
      .then(setInvoices)
      .catch((e) => setError(e instanceof Error ? e.message : "取得に失敗しました"));
  }, [periodId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleGenerate = async () => {
    setGenerating(true);
    setError(null);
    try {
      await generatePeriod(periodId);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成に失敗しました");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <main className="wide">
      <header className="page-head">
        <div>
          <h1>請求書</h1>
          <p className="lede">税率ごとに1通。明細書は行クリックで開けます。</p>
        </div>
        <div className="actions">
          <button className="btn primary" onClick={handleGenerate} disabled={generating}>
            {generating ? "生成しています…" : "明細書・請求書を生成する"}
          </button>
          <Link href={`/periods/${periodId}/contracts`} className="btn">
            リスト表へ
          </Link>
          <Link href="/periods" className="btn">
            請求期間一覧へ
          </Link>
        </div>
      </header>

      <SkipStatementPreview periodId={periodId} />

      {error && <p className="err">{error}</p>}

      {invoices && invoices.length === 0 && (
        <div className="empty">
          <p>まだ請求書が生成されていません。</p>
          <p className="sub">
            上の「明細書・請求書を生成する」ボタンを押すと、取り込んだ明細から
            契約×請求グループ単位で明細書を作り、税率ごとに請求書へ積み上げます。
          </p>
        </div>
      )}

      {invoices && invoices.length > 0 && (
        <div className="invoice-list">
          {invoices.map((inv) => (
            <InvoiceCard key={inv.id} invoice={inv} />
          ))}
        </div>
      )}
    </main>
  );
}
