/**
 * P-01 請求期間一覧。取込の起点。
 */

import Link from "next/link";
import { fetchPeriods, formatDate, formatYen, type Period } from "@/lib/api";

export const dynamic = "force-dynamic";

function StatusChip({ status }: { status: Period["status"] }) {
  const confirmed = status === "CONFIRMED";
  return (
    <span className={`chip ${confirmed ? "locked" : "draft"}`}>
      {confirmed ? "確定済" : "取込済"}
    </span>
  );
}

export default async function PeriodsPage() {
  let periods: Period[] = [];
  let error: string | null = null;

  try {
    periods = await fetchPeriods();
  } catch (e) {
    error = e instanceof Error ? e.message : "APIに接続できません";
  }

  return (
    <main>
      <header className="page-head">
        <div>
          <h1>請求期間</h1>
          <p className="lede">
            月ごとの取込状況。新しい月を取り込むには右のボタンから。
          </p>
        </div>
        <Link href="/periods/import" className="btn primary">
          Excelを取り込む
        </Link>
      </header>

      {error && <p className="err">{error}</p>}

      {!error && periods.length === 0 && (
        <div className="empty">
          <p>まだ取り込まれた請求期間がありません。</p>
          <p className="sub">
            基幹システムから出力した Excel を取り込むと、ここに月ごとに並びます。
          </p>
          <Link href="/periods/import" className="btn primary">
            最初のExcelを取り込む
          </Link>
        </div>
      )}

      {periods.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>請求期間</th>
                <th>状態</th>
                <th className="num">契約</th>
                <th className="num">明細行</th>
                <th className="num">税抜金額</th>
                <th>最終更新</th>
              </tr>
            </thead>
            <tbody>
              {periods.map((p) => (
                <tr key={p.id}>
                  <td>
                    <span className="label">{p.label}</span>
                    <span className="range">
                      {formatDate(p.start_date)} 〜 {formatDate(p.end_date)}
                    </span>
                  </td>
                  <td>
                    <StatusChip status={p.status} />
                  </td>
                  <td className="num">{p.contract_count.toLocaleString("ja-JP")}</td>
                  <td className="num">{p.line_count.toLocaleString("ja-JP")}</td>
                  <td className="num strong">{formatYen(p.total_ex_tax)}</td>
                  <td className="muted">{formatDate(p.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {periods.length > 0 && (
        <p className="note">
          税抜金額は請求対象の明細行のみを集計しています（値引行は除外）。
        </p>
      )}
    </main>
  );
}
