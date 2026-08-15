"use client";

/**
 * P-05 請求明細書 詳細・編集。
 *
 * 紙面レイアウトを再現し、数量・基本料・単価・日数/月数の4項目だけを
 * その場編集できる。金額は編集不可（生成列が自動計算）。1回の編集で
 * 明細行・明細書・請求書の3階層の合計が連動する。
 *
 * 確定済み期間は全セル読み取り専用。
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  type EditableField,
  type StatementDetail,
  type StatementLine,
  fetchStatementDetail,
  formatFieldLabel,
  formatUnitPriceType,
  formatYen,
  patchLine,
  resetLine,
} from "@/lib/api";

type EditingCell = { lineId: number; field: EditableField } | null;

const COLUMNS: { field: EditableField; label: string; align: "num" }[] = [
  { field: "quantity", label: "数量", align: "num" },
  { field: "base_charge", label: "基本料", align: "num" },
  { field: "unit_price", label: "単価", align: "num" },
  { field: "duration", label: "日数/月数", align: "num" },
];

function toEditValue(v: string | number | null): string {
  if (v === null || v === undefined) return "";
  return String(v);
}

export default function StatementDetailPage() {
  const params = useParams<{ id: string }>();
  const statementId = Number(params.id);

  const [data, setData] = useState<StatementDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditingCell>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Set<number>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    fetchStatementDetail(statementId)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "取得に失敗しました"));
  }, [statementId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const locked = data?.period_status === "CONFIRMED";

  const startEdit = (line: StatementLine, field: EditableField) => {
    if (locked || pending.has(line.id)) return;
    setEditing({ lineId: line.id, field });
    setDraft(toEditValue(line[field] as string | number | null));
  };

  const cancelEdit = () => {
    setEditing(null);
    setDraft("");
  };

  const commitEdit = async (moveNext: boolean) => {
    if (!editing || !data) return;
    const { lineId, field } = editing;

    const raw = draft.trim();
    if (field === "quantity" && raw === "") {
      alert("数量は空にできません。");
      return;
    }
    const value = raw === "" ? null : Number(raw);
    if (raw !== "" && Number.isNaN(value)) {
      alert("数値を入力してください。");
      return;
    }

    const line = data.lines.find((l) => l.id === lineId);
    const currentValue = line ? toEditValue(line[field] as string | number | null) : "";
    if (raw === currentValue) {
      setEditing(null);
      return;
    }

    setPending((s) => new Set(s).add(lineId));
    try {
      const changes: Partial<Record<EditableField, number | null>> = {};
      changes[field] = value;
      const result = await patchLine(lineId, changes);
      setData((d) => {
        if (!d) return d;
        return {
          ...d,
          statement: result.statement
            ? { ...d.statement, ...result.statement }
            : d.statement,
          lines: d.lines.map((l) => (l.id === lineId ? { ...l, ...result.line } : l)),
        };
      });

      if (moveNext) {
        const idx = data.lines.findIndex((l) => l.id === lineId);
        const colIdx = COLUMNS.findIndex((c) => c.field === field);
        const nextCol = COLUMNS[colIdx + 1];
        if (nextCol) {
          setEditing({ lineId, field: nextCol.field });
          const nextLine = data.lines[idx];
          setDraft(toEditValue(nextLine[nextCol.field] as string | number | null));
          setPending((s) => {
            const n = new Set(s);
            n.delete(lineId);
            return n;
          });
          return;
        }
        const nextLine = data.lines[idx + 1];
        if (nextLine) {
          setEditing({ lineId: nextLine.id, field: COLUMNS[0].field });
          setDraft(toEditValue(nextLine[COLUMNS[0].field] as string | number | null));
          setPending((s) => {
            const n = new Set(s);
            n.delete(lineId);
            return n;
          });
          return;
        }
      }
      setEditing(null);
    } catch (e) {
      alert(e instanceof Error ? e.message : "更新に失敗しました");
    } finally {
      setPending((s) => {
        const n = new Set(s);
        n.delete(lineId);
        return n;
      });
    }
  };

  const handleReset = async (lineId: number) => {
    if (!confirm("この行を取込時の値に戻しますか？")) return;
    setPending((s) => new Set(s).add(lineId));
    try {
      const result = await resetLine(lineId);
      setData((d) => {
        if (!d) return d;
        return {
          ...d,
          statement: result.statement ? { ...d.statement, ...result.statement } : d.statement,
          lines: d.lines.map((l) => (l.id === lineId ? { ...l, ...result.line } : l)),
        };
      });
    } catch (e) {
      alert(e instanceof Error ? e.message : "取消に失敗しました");
    } finally {
      setPending((s) => {
        const n = new Set(s);
        n.delete(lineId);
        return n;
      });
    }
  };

  if (error) return <main className="wide"><p className="err">{error}</p></main>;
  if (!data) return <main className="wide"><p className="muted">読み込んでいます…</p></main>;

  const { statement, lines } = data;

  return (
    <main className="wide">
      <header className="page-head">
        <div>
          <h1>請求明細書</h1>
          <p className="lede">
            {locked ? (
              <span className="lock-note">🔒 確定済みのため編集できません</span>
            ) : (
              "セルをクリックして編集。Enter/Tabで次のセルへ、Escで取消。"
            )}
          </p>
        </div>
        <Link href={`/periods/${data.period_id}/statements`} className="btn">
          明細書一覧へ
        </Link>
      </header>

      <div className="paper-head">
        <dl>
          <div>
            <dt>得意先</dt>
            <dd>{statement.client_name}</dd>
          </div>
          <div>
            <dt>管理番号</dt>
            <dd className="mono">{statement.contract_no}</dd>
          </div>
          <div>
            <dt>納品先</dt>
            <dd>{statement.site_name}</dd>
          </div>
          <div>
            <dt>請求期間</dt>
            <dd>{data.period_label}</dd>
          </div>
          <div>
            <dt>区分</dt>
            <dd>
              <span className={`tag ${statement.billing_group === "COUNTER" ? "counter" : ""}`}>
                {statement.billing_group === "COUNTER" ? "カウンタ" : "備品"}
              </span>
            </dd>
          </div>
        </dl>
      </div>

      <div className="scroll">
        <table className="statement-table">
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
              <th></th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => {
              const isPending = pending.has(line.id);
              return (
                <tr key={line.id} className={line.is_edited ? "row-edited" : ""}>
                  <td className="muted">{line.delivery_date ?? "—"}</td>
                  <td className="mono">{line.item_code}</td>
                  <td>{line.item_name}</td>
                  {COLUMNS.map((col) => {
                    const isEditingThis =
                      editing?.lineId === line.id && editing.field === col.field;
                    const value = line[col.field] as string | number | null;
                    const srcValue = line[`src_${col.field}` as keyof StatementLine] as
                      | string
                      | number
                      | null;
                    const changed =
                      toEditValue(value) !== toEditValue(srcValue);
                    return (
                      <td
                        key={col.field}
                        className={`num editable ${changed ? "cell-edited" : ""}`}
                        onClick={() => startEdit(line, col.field)}
                        title={changed ? `元: ${srcValue ?? "（空）"}` : undefined}
                      >
                        {isEditingThis ? (
                          <input
                            ref={inputRef}
                            className="cell-input"
                            type="number"
                            value={draft}
                            disabled={isPending}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === "Tab") {
                                e.preventDefault();
                                commitEdit(true);
                              } else if (e.key === "Escape") {
                                cancelEdit();
                              }
                            }}
                            onBlur={() => commitEdit(false)}
                          />
                        ) : (
                          <span>{value ?? "—"}</span>
                        )}
                      </td>
                    );
                  })}
                  <td className="muted">{formatUnitPriceType(line.unit_price_type)}</td>
                  <td className="num strong">{formatYen(line.amount)}</td>
                  <td>
                    {line.is_edited && !locked && (
                      <button
                        className="btn ghost small"
                        onClick={() => handleReset(line.id)}
                        disabled={isPending}
                      >
                        戻す
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="paper-foot">
        <dl>
          <div>
            <dt>税抜</dt>
            <dd>{formatYen(statement.total_ex_tax)}</dd>
          </div>
          <div>
            <dt>消費税</dt>
            <dd>{formatYen(statement.tax_amount)}</dd>
          </div>
          <div className="grand">
            <dt>合計</dt>
            <dd>{formatYen(statement.total_amount)}</dd>
          </div>
        </dl>
      </div>
    </main>
  );
}
