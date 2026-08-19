"use client";

/**
 * P-07 発行前チェック。
 *
 * 4種類の観点で異常を一覧する。
 *   請求漏れ   請求対象の明細があるのに、どの明細書にも入っていない
 *   金額0円    請求対象の明細行なのに金額が0円
 *   期間外     明細のレンタル期間が請求期間と重ならない
 *   前月比     契約単位で直前の請求期間と比べ、新規・消滅・急増急減を検出
 *
 * 「前月比」はVBA原本（リスト表と営業データの差分）とは意味が異なる。
 * VBA原本は営業部が別途持つ見込みデータとの突き合わせで、この
 * ドメインモデルには存在しないデータソースを前提にしていた。ここでは
 * 文字通り「直前の請求期間とDB上で比較する」方式で作り直している
 * （docs/vba-analysis.md参照）。
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  type ValidationCategory,
  type ValidationIssue,
  type ValidationResult,
  fetchValidation,
  formatValidationCategory,
  formatYen,
} from "@/lib/api";

// 表示順。severityは各カテゴリで固定なので、この順に並べるだけで
// 重要度の高いものが自然に上に来る。
const CATEGORY_ORDER: ValidationCategory[] = [
  "MISSING_STATEMENT",
  "ZERO_AMOUNT",
  "OUT_OF_PERIOD",
  "AMOUNT_CHANGED",
  "NEW_CONTRACT",
  "VANISHED_CONTRACT",
];

function IssueRow({ issue }: { issue: ValidationIssue }) {
  return (
    <li>
      <div className="issue-head">
        {issue.contract_no && <span className="mono">{issue.contract_no}</span>}
        {issue.client_name && <span>{issue.client_name}</span>}
        {issue.site_name && <span className="issue-sub">{issue.site_name}</span>}
        {issue.item_name && <span className="issue-sub">{issue.item_name}</span>}
        {issue.amount !== null && (
          <span className="mono issue-amount">今期 {formatYen(issue.amount)}円</span>
        )}
        {issue.previous_amount !== null && (
          <span className="mono issue-amount prev">前期 {formatYen(issue.previous_amount)}円</span>
        )}
      </div>
      <span className="issue-message">{issue.message}</span>
    </li>
  );
}

function IssueGroup({
  category,
  issues,
}: {
  category: ValidationCategory;
  issues: ValidationIssue[];
}) {
  if (issues.length === 0) return null;
  const severityClass = issues[0].severity.toLowerCase();
  const chipClass = { high: "ng", medium: "warn", info: "info" }[severityClass] ?? "warn";
  return (
    <div className={`validation-group ${severityClass}`}>
      <h3>
        {formatValidationCategory(category)}
        <span className={`chip ${chipClass}`}>{issues.length}件</span>
      </h3>
      <ul>
        {issues.map((issue, i) => (
          <IssueRow key={i} issue={issue} />
        ))}
      </ul>
    </div>
  );
}

export default function ValidatePage() {
  const params = useParams<{ id: string }>();
  const periodId = Number(params.id);

  const [result, setResult] = useState<ValidationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchValidation(periodId)
      .then(setResult)
      .catch((e) => setError(e instanceof Error ? e.message : "取得に失敗しました"))
      .finally(() => setLoading(false));
  }, [periodId]);

  const byCategory = (category: ValidationCategory) =>
    result?.issues.filter((i) => i.category === category) ?? [];

  return (
    <main className="wide">
      <header className="page-head">
        <div>
          <h1>発行前チェック</h1>
          <p className="lede">
            請求漏れ・金額0円・期間外・前月比の異常をまとめて確認します。
          </p>
        </div>
        <div className="actions">
          <Link href={`/periods/${periodId}/contracts`} className="btn">
            リスト表へ
          </Link>
          <Link href={`/periods/${periodId}/invoices`} className="btn">
            請求書へ
          </Link>
          <Link href={`/periods/${periodId}/export`} className="btn">
            PDF出力へ
          </Link>
        </div>
      </header>

      {error && <p className="err">{error}</p>}
      {loading && <p className="muted">確認しています…</p>}

      {!loading && result && (
        <>
          {result.previous_period_label === null && (
            <p className="note warn">
              比較対象の前月データがありません。「新規契約」「消滅契約」「金額変動」は
              判定できません。
            </p>
          )}

          <div className="summarybar">
            <span>
              <span className="chip ng">{result.summary.high}</span> 重要
            </span>
            <span>
              <span className="chip warn">{result.summary.medium}</span> 注意
            </span>
            <span>
              <span className="chip info">{result.summary.info}</span> 情報
            </span>
            {result.previous_period_label && (
              <span className="muted">比較対象: {result.previous_period_label}</span>
            )}
          </div>

          {result.issues.length === 0 && (
            <div className="empty">
              <p>問題は見つかりませんでした。</p>
              <p className="sub">請求漏れ・金額0円・期間外・前月比のいずれにも異常はありません。</p>
            </div>
          )}

          {CATEGORY_ORDER.map((category) => (
            <IssueGroup key={category} category={category} issues={byCategory(category)} />
          ))}
        </>
      )}
    </main>
  );
}
