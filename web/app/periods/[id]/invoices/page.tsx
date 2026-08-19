"use client";

/**
 * P-06 請求書。
 *
 * 税率ごとに1通（10%・8%）。各請求書の下に明細書を一覧し、行クリックで
 * 対応する明細書（P-05）へ遷移する（VBAのダブルクリック機能の正統進化）。
 *
 * 明細不要（skip_statement）は生成時のスナップショットにしか効かない
 * （リスト表で切り替えただけでは既存の請求書は変わらない）ため、この
 * 画面を開くたびに未確定の請求期間なら自動で生成し直す。「生成済みの
 * 一覧が最新の設定を反映しているか」を利用者が気にする必要をなくし、
 * 常にリスト表の現在の明細不要設定がそのまま反映された状態で開く。
 * 確定済み期間（読み取り専用）は自動生成の対象外（409を静かに無視）。
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  type ContractFilters,
  type ContractListResponse,
  type InvoiceRow,
  type Period,
  type PeriodInvoiceSummary,
  type StatementSummaryRow,
  confirmPeriod,
  fetchContracts,
  fetchInvoiceStatements,
  fetchInvoices,
  fetchPeriod,
  formatDateTime,
  formatYen,
  generatePeriod,
  unconfirmPeriod,
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

function SkipStatementPreview({
  periodId,
  data,
}: {
  periodId: number;
  data: ContractListResponse | null;
}) {
  const [expanded, setExpanded] = useState(false);

  if (!data || data.summary.count === 0) return null;

  return (
    <div className="preview-panel">
      <div className="preview-head">
        <span>
          明細不要により <strong className="preview-count">{data.summary.count}</strong> 件
          （税抜 {formatYen(data.summary.total_ex_tax)} 円）が除外されています。
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

function ConfirmBar({
  period,
  revision,
  onChanged,
}: {
  period: Period;
  revision: number | null;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [unlocking, setUnlocking] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = async () => {
    if (
      !confirm(
        `${period.label} を確定します。確定後は明細の手修正・再生成ができなくなります。よろしいですか？`
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await confirmPeriod(period.id);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "確定に失敗しました");
    } finally {
      setBusy(false);
    }
  };

  const handleUnconfirm = async () => {
    if (!reason.trim()) {
      setError("確定解除の理由を入力してください。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await unconfirmPeriod(period.id, reason.trim());
      setUnlocking(false);
      setReason("");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "確定解除に失敗しました");
    } finally {
      setBusy(false);
    }
  };

  if (period.status === "CONFIRMED") {
    return (
      <div className="confirm-bar locked">
        <div className="confirm-status">
          <span className="lock-note">🔒 確定済み{revision ? `（第${revision}版）` : ""}</span>
          <span className="muted small">確定日時 {formatDateTime(period.confirmed_at)}</span>
        </div>
        {!unlocking ? (
          <button className="btn ghost small" onClick={() => setUnlocking(true)} disabled={busy}>
            確定解除する
          </button>
        ) : (
          <div className="unlock-form">
            <input
              type="text"
              placeholder="確定解除の理由（必須）"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={busy}
            />
            <button className="btn small" onClick={handleUnconfirm} disabled={busy}>
              {busy ? "実行しています…" : "実行"}
            </button>
            <button
              className="btn ghost small"
              onClick={() => {
                setUnlocking(false);
                setReason("");
                setError(null);
              }}
              disabled={busy}
            >
              キャンセル
            </button>
          </div>
        )}
        {error && <p className="err small">{error}</p>}
      </div>
    );
  }

  return (
    <div className="confirm-bar">
      <button className="btn primary" onClick={handleConfirm} disabled={busy}>
        {busy ? "確定しています…" : "確定する"}
      </button>
      {error && <p className="err small">{error}</p>}
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
  const [summary, setSummary] = useState<PeriodInvoiceSummary | null>(null);
  const [excluded, setExcluded] = useState<ContractListResponse | null>(null);
  const [period, setPeriod] = useState<Period | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(true);
  const [generating, setGenerating] = useState(false);

  const load = useCallback(() => {
    fetchInvoices(periodId)
      .then((d) => {
        setInvoices(d.items);
        setSummary(d.summary);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "取得に失敗しました"));
  }, [periodId]);

  const loadExcluded = useCallback(() => {
    fetchContracts(periodId, SKIP_ONLY_FILTERS)
      .then(setExcluded)
      .catch(() => {
        // 除外プレビューは補助情報。取得に失敗しても本体は止めない。
      });
  }, [periodId]);

  const loadPeriod = useCallback(() => {
    fetchPeriod(periodId)
      .then(setPeriod)
      .catch(() => {
        // 確定バーの表示に使うだけの補助情報。取得に失敗しても本体は止めない。
      });
  }, [periodId]);

  // 画面を開くたびに、リスト表の現在の明細不要設定を反映した状態へ
  // 自動で生成し直す。確定済み期間は洗い替え自体が意味を持たない
  // （手修正も再生成もできない。金額は確定時のスナップショットが正）
  // ため、先に期間の状態を見てから確定済みなら generate() 自体を呼ばない。
  //
  // AbortControllerで実リクエストごと打ち切る。React StrictModeは開発時
  // このeffectをmount→cleanup→mountと二重発火させるため、素朴に
  // フラグだけで結果を無視すると、リクエスト自体は2本とも生成APIへ
  // 飛んでしまう。generate()は洗い替え（invoiceの一意制約に一度に
  // 1本しか書けない）なので、同時に2本走ると片方が500で落ち、
  // しかもそのエラー表示が成功後も消えずに残る不具合をmoney-auditで
  // 実際に確認した。cleanupでabortすれば、StrictModeの1本目は
  // レスポンスを待たずに打ち切られ、実際に完走するのは1本だけになる。
  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    (async () => {
      try {
        const p = await fetchPeriod(periodId);
        if (controller.signal.aborted) return;
        setPeriod(p);
        if (p.status !== "CONFIRMED") {
          await generatePeriod(periodId, controller.signal);
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        const expected = e instanceof ApiError && (e.status === 409 || e.status === 422);
        if (!expected) {
          setError(e instanceof Error ? e.message : "生成に失敗しました");
        }
      } finally {
        if (!controller.signal.aborted) {
          setSyncing(false);
          load();
          loadExcluded();
        }
      }
    })();
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [periodId]);

  const handleGenerate = async () => {
    setGenerating(true);
    setError(null);
    try {
      await generatePeriod(periodId);
      load();
      loadExcluded();
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
          <p className="lede">
            税率ごとに1通。明細書は行クリックで開けます。開くたびにリスト表の
            明細不要設定を反映して自動更新します。
          </p>
        </div>
        <div className="actions">
          {period?.status !== "CONFIRMED" && (
            <button className="btn primary" onClick={handleGenerate} disabled={generating}>
              {generating ? "生成しています…" : "生成し直す"}
            </button>
          )}
          <Link href={`/periods/${periodId}/contracts`} className="btn">
            リスト表へ
          </Link>
          <Link href={`/periods/${periodId}/validate`} className="btn">
            発行前チェックへ
          </Link>
          <Link href={`/periods/${periodId}/export`} className="btn">
            PDF出力へ
          </Link>
          <Link href="/periods" className="btn">
            請求期間一覧へ
          </Link>
        </div>
      </header>

      {period && (
        <ConfirmBar
          period={period}
          revision={invoices?.[0]?.revision ?? null}
          onChanged={() => {
            loadPeriod();
            load();
            loadExcluded();
          }}
        />
      )}

      {period?.status !== "CONFIRMED" && (
        <SkipStatementPreview periodId={periodId} data={excluded} />
      )}

      {error && <p className="err">{error}</p>}

      {syncing && <p className="muted">最新の設定を反映しています…</p>}

      {!syncing && invoices && invoices.length === 0 && (
        <div className="empty">
          <p>請求対象の明細がありません。</p>
          <p className="sub">
            取り込んだ明細が0件か、すべて明細不要・値引で除外されています。
            リスト表で設定を確認してください。
          </p>
        </div>
      )}

      {!syncing && invoices && invoices.length > 0 && (
        <>
          {summary && (
            <div className="summarybar">
              <span>
                税抜合計 <strong>{formatYen(summary.total_ex_tax)}</strong> 円
              </span>
              <span>
                消費税合計 <strong>{formatYen(summary.tax_amount)}</strong> 円
              </span>
              <span>
                合計 <strong>{formatYen(summary.total_amount)}</strong> 円
              </span>
            </div>
          )}
          <div className="invoice-list">
            {invoices.map((inv) => (
              <InvoiceCard key={inv.id} invoice={inv} />
            ))}
          </div>
        </>
      )}
    </main>
  );
}
