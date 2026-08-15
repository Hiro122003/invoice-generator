"use client";

/**
 * P-04 請求明細書一覧。
 *
 * 請求期間内の全明細書（10%・8%を横断）を、会社・税率・請求グループで
 * 絞り込む。修正済みの明細書にはマークが付く。行クリックでP-05へ。
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  type PeriodStatementFilters,
  type PeriodStatementRow,
  fetchPeriodStatements,
  formatYen,
} from "@/lib/api";

const EMPTY_FILTERS: PeriodStatementFilters = { client: "", tax: "", group: "" };

export default function PeriodStatementsPage() {
  const params = useParams<{ id: string }>();
  const periodId = Number(params.id);

  const [filters, setFilters] = useState<PeriodStatementFilters>(EMPTY_FILTERS);
  const [rows, setRows] = useState<PeriodStatementRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setLoading(true);
      setError(null);
      fetchPeriodStatements(periodId, filters)
        .then(setRows)
        .catch((e) => setError(e instanceof Error ? e.message : "取得に失敗しました"))
        .finally(() => setLoading(false));
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [periodId, filters]);

  const setField = <K extends keyof PeriodStatementFilters>(
    key: K,
    value: PeriodStatementFilters[K]
  ) => setFilters((f) => ({ ...f, [key]: value }));

  const editedCount = rows?.filter((r) => r.is_edited).length ?? 0;

  return (
    <main className="wide">
      <header className="page-head">
        <div>
          <h1>請求明細書一覧</h1>
          <p className="lede">会社・税率・請求グループで絞り込み。行クリックで詳細・編集へ。</p>
        </div>
        <Link href={`/periods/${periodId}/invoices`} className="btn">
          請求書へ
        </Link>
      </header>

      <div className="filterbar">
        <input
          type="text"
          placeholder="得意先"
          value={filters.client}
          onChange={(e) => setField("client", e.target.value)}
        />
        <select
          value={filters.tax}
          onChange={(e) => setField("tax", e.target.value as PeriodStatementFilters["tax"])}
        >
          <option value="">税区分（すべて）</option>
          <option value="STANDARD">10%</option>
          <option value="REDUCED">8%</option>
        </select>
        <select
          value={filters.group}
          onChange={(e) => setField("group", e.target.value as PeriodStatementFilters["group"])}
        >
          <option value="">請求グループ（すべて）</option>
          <option value="EQUIPMENT">備品</option>
          <option value="COUNTER">カウンタ</option>
        </select>
      </div>

      {rows && (
        <div className="summarybar">
          <span>
            <strong>{rows.length.toLocaleString("ja-JP")}</strong> 枚
          </span>
          {editedCount > 0 && (
            <span className="warn">
              うち <strong>{editedCount}</strong> 枚に手修正あり
            </span>
          )}
        </div>
      )}

      {error && <p className="err">{error}</p>}
      {loading && <p className="muted">読み込んでいます…</p>}

      {!loading && rows && rows.length === 0 && (
        <div className="empty">
          <p>明細書がまだありません。</p>
          <p className="sub">
            <Link href={`/periods/${periodId}/invoices`}>請求書ページ</Link>
            から生成してください。
          </p>
        </div>
      )}

      {!loading && rows && rows.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>税区分</th>
                <th>契約番号</th>
                <th>得意先</th>
                <th>現場</th>
                <th>区分</th>
                <th className="num">税抜</th>
                <th className="num">合計</th>
                <th>状態</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="clickable">
                  <td>
                    <span className={`tag ${r.tax_category === "REDUCED" ? "reduced" : ""}`}>
                      {r.tax_category === "REDUCED" ? "8%" : "10%"}
                    </span>
                  </td>
                  <td className="mono">
                    <Link href={`/statements/${r.id}`} className="celllink">
                      {r.contract_no}
                    </Link>
                  </td>
                  <td>{r.client_name}</td>
                  <td>{r.site_name}</td>
                  <td>
                    <span className={`tag ${r.billing_group === "COUNTER" ? "counter" : ""}`}>
                      {r.billing_group === "COUNTER" ? "カウンタ" : "備品"}
                    </span>
                  </td>
                  <td className="num">{formatYen(r.total_ex_tax)}</td>
                  <td className="num strong">{formatYen(r.total_amount)}</td>
                  <td>
                    {r.is_edited && (
                      <span className="tag edited">修正済 {r.edited_line_count}</span>
                    )}
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
