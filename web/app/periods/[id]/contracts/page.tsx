"use client";

/**
 * P-03 リスト表。
 *
 * 得意先・納品先・契約番号でフィルタし、明細不要（skip_statement）を
 * その場で切り替える。行をクリックするとその契約の明細行を展開する
 * （ドリルダウン）。
 */

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  PUBLIC_API_BASE,
  type BillingLineRow,
  type ContractFilters,
  type ContractListResponse,
  type ContractRow,
  buildContractQuery,
  fetchContractLines,
  fetchContracts,
  formatUnitPriceType,
  formatYen,
  updateSkipStatement,
} from "@/lib/api";

const EMPTY_FILTERS: ContractFilters = {
  client: "",
  site: "",
  contract_no: "",
  tax: "",
  group: "",
  skip_statement: "",
  min_amount: "",
  max_amount: "",
};

// 一括解除ボタンの対象を数えるときに使う。表示中の絞り込み条件とは
// 独立に「この請求期間で明細不要になっている契約」を常に指す。
const SKIP_ONLY_FILTERS: ContractFilters = { ...EMPTY_FILTERS, skip_statement: "true" };

function ContractLines({
  periodId,
  contractId,
}: {
  periodId: number;
  contractId: number;
}) {
  const [lines, setLines] = useState<BillingLineRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchContractLines(periodId, contractId)
      .then((data) => {
        if (!cancelled) setLines(data);
      })
      .catch((e) => {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "取得に失敗しました");
      });
    return () => {
      cancelled = true;
    };
  }, [periodId, contractId]);

  if (error) return <p className="err small">{error}</p>;
  if (!lines) return <p className="muted small">読み込んでいます…</p>;
  if (lines.length === 0) return <p className="muted small">明細がありません。</p>;

  return (
    <div className="scroll lines">
      <table className="lines-table">
        <thead>
          <tr>
            <th>納品日</th>
            <th>品番</th>
            <th>品名</th>
            <th className="num">数量</th>
            <th className="num">基本料</th>
            <th className="num">単価</th>
            <th>種別</th>
            <th className="num">日数/月数</th>
            <th className="num">金額</th>
            <th>区分</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.id} className={l.is_billable ? "" : "excluded"}>
              <td className="muted">{l.delivery_date ?? "—"}</td>
              <td className="mono">{l.item_code}</td>
              <td>
                {l.item_name}
                {l.is_edited && <span className="edited-mark" title="手修正あり" />}
                {!l.is_billable && <span className="tag ng">請求対象外</span>}
              </td>
              <td className="num">{l.quantity}</td>
              <td className="num">{l.base_charge ?? "—"}</td>
              <td className="num">{l.unit_price ?? "—"}</td>
              <td className="muted">{formatUnitPriceType(l.unit_price_type)}</td>
              <td className="num">{l.duration ?? "—"}</td>
              <td className="num strong">{formatYen(l.amount)}</td>
              <td>
                <span className={`tag ${l.tax_category === "REDUCED" ? "reduced" : ""}`}>
                  {l.tax_category === "REDUCED" ? "8%" : "10%"}
                </span>
                {l.billing_group === "COUNTER" && (
                  <span className="tag counter">カウンタ</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ContractListPage() {
  const params = useParams<{ id: string }>();
  const periodId = Number(params.id);

  // P-06（請求書）の発行前プレビューから「リスト表で調整する」で
  // 遷移してきたとき、?skip_statement=true を初期フィルタとして
  // 引き継ぐ。それ以外の起動経路では通常どおり空フィルタ。
  const searchParams = useSearchParams();
  const [filters, setFilters] = useState<ContractFilters>(() => {
    const initialSkip = searchParams.get("skip_statement");
    return initialSkip === "true" || initialSkip === "false"
      ? { ...EMPTY_FILTERS, skip_statement: initialSkip }
      : EMPTY_FILTERS;
  });
  const [data, setData] = useState<ContractListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [pending, setPending] = useState<Set<number>>(new Set());
  const [skipCount, setSkipCount] = useState<number | null>(null);
  const [bulkClearing, setBulkClearing] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 表示中の絞り込みとは別に「一括解除」ボタンの件数バッジ用に、
  // この請求期間全体での明細不要件数を持っておく。
  const loadSkipCount = useCallback(() => {
    fetchContracts(periodId, SKIP_ONLY_FILTERS)
      .then((d) => setSkipCount(d.summary.count))
      .catch(() => {
        // 件数バッジは補助情報。取得に失敗しても画面本体は止めない。
      });
  }, [periodId]);

  useEffect(() => {
    loadSkipCount();
  }, [loadSkipCount]);

  const load = useCallback(
    (f: ContractFilters) => {
      setLoading(true);
      setError(null);
      fetchContracts(periodId, f)
        .then(setData)
        .catch((e) =>
          setError(e instanceof Error ? e.message : "取得に失敗しました")
        )
        .finally(() => setLoading(false));
    },
    [periodId]
  );

  // テキスト系フィルタは入力の都度ではなく、少し待ってから検索する
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => load(filters), 350);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, periodId]);

  const setField = <K extends keyof ContractFilters>(
    key: K,
    value: ContractFilters[K]
  ) => setFilters((f) => ({ ...f, [key]: value }));

  const toggleSkip = async (row: ContractRow) => {
    setPending((s) => new Set(s).add(row.id));
    try {
      const updated = await updateSkipStatement(row.id, !row.skip_statement);
      setData((d) =>
        d
          ? {
              ...d,
              items: d.items.map((r) =>
                r.id === row.id ? { ...r, skip_statement: updated.skip_statement } : r
              ),
            }
          : d
      );
      if (row.skip_statement !== updated.skip_statement) {
        setSkipCount((c) => (c === null ? c : c + (updated.skip_statement ? 1 : -1)));
      }
    } catch (e) {
      alert(e instanceof Error ? e.message : "更新に失敗しました");
    } finally {
      setPending((s) => {
        const next = new Set(s);
        next.delete(row.id);
        return next;
      });
    }
  };

  const handleBulkClear = async () => {
    if (!skipCount) return;
    if (!confirm(`明細不要になっている ${skipCount} 件をすべて解除しますか？`)) return;

    setBulkClearing(true);
    try {
      // 表示中の絞り込みに関係なく、この請求期間で明細不要な契約を
      // 取り直してから全件解除する（見えていない行の取りこぼしを防ぐ）。
      const targets = await fetchContracts(periodId, SKIP_ONLY_FILTERS);
      const results = await Promise.allSettled(
        targets.items.map((c) => updateSkipStatement(c.id, false))
      );
      const failed = results.filter((r) => r.status === "rejected").length;
      if (failed > 0) {
        alert(`${failed}件は解除に失敗しました。もう一度お試しください。`);
      }
      load(filters);
      loadSkipCount();
    } catch (e) {
      alert(e instanceof Error ? e.message : "一括解除に失敗しました");
    } finally {
      setBulkClearing(false);
    }
  };

  const exportUrl = useMemo(() => {
    const qs = buildContractQuery(filters);
    return `${PUBLIC_API_BASE}/api/periods/${periodId}/contracts/export${qs ? `?${qs}` : ""}`;
  }, [periodId, filters]);

  const hasFilters = Object.values(filters).some((v) => v);

  return (
    <main className="wide">
      <header className="page-head">
        <div>
          <h1>リスト表</h1>
          <p className="lede">
            契約を得意先・納品先・契約番号で絞り込み。明細不要はここで設定すると翌月以降も引き継がれます。
          </p>
        </div>
        <div className="actions">
          <a href={exportUrl} className="btn">
            CSV出力
          </a>
          <Link href={`/periods/${periodId}/statements`} className="btn">
            請求明細書一覧へ
          </Link>
          <Link href={`/periods/${periodId}/invoices`} className="btn">
            請求書ページへ
          </Link>
          <Link href="/periods" className="btn">
            請求期間一覧へ
          </Link>
        </div>
      </header>

      <div className="filterbar">
        <input
          type="text"
          placeholder="得意先"
          value={filters.client}
          onChange={(e) => setField("client", e.target.value)}
        />
        <input
          type="text"
          placeholder="納品先名称"
          value={filters.site}
          onChange={(e) => setField("site", e.target.value)}
        />
        <input
          type="text"
          placeholder="契約番号"
          value={filters.contract_no}
          onChange={(e) => setField("contract_no", e.target.value)}
        />
        <select
          value={filters.tax}
          onChange={(e) => setField("tax", e.target.value as ContractFilters["tax"])}
        >
          <option value="">税区分（すべて）</option>
          <option value="STANDARD">10%を含む</option>
          <option value="REDUCED">8%を含む</option>
        </select>
        <select
          value={filters.group}
          onChange={(e) =>
            setField("group", e.target.value as ContractFilters["group"])
          }
        >
          <option value="">請求グループ（すべて）</option>
          <option value="EQUIPMENT">備品を含む</option>
          <option value="COUNTER">カウンタを含む</option>
        </select>
        <select
          value={filters.skip_statement}
          onChange={(e) =>
            setField(
              "skip_statement",
              e.target.value as ContractFilters["skip_statement"]
            )
          }
        >
          <option value="">明細不要（すべて）</option>
          <option value="true">明細不要のみ</option>
          <option value="false">明細不要でない</option>
        </select>
        {!!skipCount && (
          <button className="btn ghost" onClick={handleBulkClear} disabled={bulkClearing}>
            {bulkClearing ? "解除しています…" : `明細不要を一括解除（${skipCount}件）`}
          </button>
        )}
        <input
          type="number"
          placeholder="金額下限"
          value={filters.min_amount}
          onChange={(e) => setField("min_amount", e.target.value)}
        />
        <input
          type="number"
          placeholder="金額上限"
          value={filters.max_amount}
          onChange={(e) => setField("max_amount", e.target.value)}
        />
        {hasFilters && (
          <button className="btn ghost" onClick={() => setFilters(EMPTY_FILTERS)}>
            条件をクリア
          </button>
        )}
      </div>

      {data && (
        <div className="summarybar">
          <span>
            <strong>{data.summary.count.toLocaleString("ja-JP")}</strong> 件
          </span>
          <span>
            税抜合計 <strong>{formatYen(data.summary.total_ex_tax)}</strong> 円
          </span>
        </div>
      )}

      {error && <p className="err">{error}</p>}
      {loading && <p className="muted">読み込んでいます…</p>}

      {!loading && data && data.items.length === 0 && (
        <div className="empty">
          <p>条件に一致する契約がありません。</p>
        </div>
      )}

      {!loading && data && data.items.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>契約番号</th>
                <th>得意先</th>
                <th>納品先名称</th>
                <th className="num">明細行</th>
                <th className="num">税抜金額</th>
                <th>区分</th>
                <th>明細不要</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <Fragment key={row.id}>
                  <tr
                    className="clickable"
                    onClick={() =>
                      setExpanded((cur) => (cur === row.id ? null : row.id))
                    }
                  >
                    <td className="mono">{row.contract_no}</td>
                    <td>{row.client_name}</td>
                    <td>
                      {row.site_name}
                      {row.address && <span className="sub">{row.address}</span>}
                    </td>
                    <td className="num">{row.line_count}</td>
                    <td className="num strong">{formatYen(row.total_ex_tax)}</td>
                    <td>
                      <span className={`tag ${row.has_reduced ? "reduced" : ""}`}>
                        {row.has_reduced && row.has_standard
                          ? "10%/8%"
                          : row.has_reduced
                            ? "8%"
                            : "10%"}
                      </span>
                      {row.has_counter && <span className="tag counter">カウンタ</span>}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button
                        className={`toggle ${row.skip_statement ? "on" : ""}`}
                        disabled={pending.has(row.id)}
                        onClick={() => toggleSkip(row)}
                        aria-pressed={row.skip_statement}
                      >
                        {row.skip_statement ? "明細不要" : "－"}
                      </button>
                    </td>
                  </tr>
                  {expanded === row.id && (
                    <tr className="expand-row">
                      <td colSpan={7}>
                        <ContractLines periodId={periodId} contractId={row.id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
