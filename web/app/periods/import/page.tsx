"use client";

/**
 * P-02 Excel取込。
 *
 * ファイルを選ぶと自動で検証だけ走り、内容を確認してから投入する。
 * 投入は洗い替え（対象月のデータを入れ替える）なので、
 * 何が起きるかを先に見せてから実行させる。
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import {
  API_BASE,
  formatDate,
  type ImportResult,
  type Issue,
  type Validation,
} from "@/lib/api";

type Phase = "idle" | "validating" | "ready" | "importing" | "done";

const SEVERITY_LABEL: Record<Issue["severity"], string> = {
  ERROR: "エラー",
  WARNING: "警告",
  INFO: "情報",
};

export default function ImportPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);

  const [phase, setPhase] = useState<Phase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const reset = () => {
    setPhase("idle");
    setFile(null);
    setValidation(null);
    setResult(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const validate = useCallback(async (f: File) => {
    setFile(f);
    setError(null);
    setValidation(null);
    setResult(null);
    setPhase("validating");

    const body = new FormData();
    body.append("file", f);
    try {
      const res = await fetch(`${API_BASE}/api/imports/validate`, {
        method: "POST",
        body,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail ?? `検証に失敗しました (${res.status})`);
      }
      setValidation(await res.json());
      setPhase("ready");
    } catch (e) {
      setError(e instanceof Error ? e.message : "検証に失敗しました");
      setPhase("idle");
    }
  }, []);

  const commit = async () => {
    if (!file) return;
    setPhase("importing");
    setError(null);

    const body = new FormData();
    body.append("file", file);
    try {
      const res = await fetch(`${API_BASE}/api/imports`, {
        method: "POST",
        body,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        const msg =
          typeof detail?.detail === "string"
            ? detail.detail
            : detail?.detail?.message ?? `取込に失敗しました (${res.status})`;
        throw new Error(msg);
      }
      setResult(await res.json());
      setPhase("done");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "取込に失敗しました");
      setPhase("ready");
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) validate(f);
  };

  const errors = validation?.issues.filter((i) => i.severity === "ERROR") ?? [];
  const others = validation?.issues.filter((i) => i.severity !== "ERROR") ?? [];

  return (
    <main>
      <header className="page-head">
        <div>
          <h1>Excelを取り込む</h1>
          <p className="lede">
            基幹システムから出力したファイルを選ぶと、まず内容を検証します。
          </p>
        </div>
        <Link href="/periods" className="btn">
          請求期間一覧へ
        </Link>
      </header>

      {phase !== "done" && (
        <div
          className={`dropzone ${dragging ? "over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xlsm"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) validate(f);
            }}
          />
          {phase === "validating" ? (
            <p>検証しています…</p>
          ) : file ? (
            <>
              <p className="filename">{file.name}</p>
              <p className="sub">別のファイルに変えるにはクリック</p>
            </>
          ) : (
            <>
              <p>ここに xlsx をドロップ</p>
              <p className="sub">またはクリックして選択</p>
            </>
          )}
        </div>
      )}

      {error && <p className="err">{error}</p>}

      {validation && phase !== "done" && (
        <section className="result">
          <h2>検証結果</h2>

          <dl className="facts">
            <div>
              <dt>請求期間</dt>
              <dd className="strong">
                {validation.period_start
                  ? `${formatDate(validation.period_start)} 〜 ${formatDate(
                      validation.period_end
                    )}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>販売先</dt>
              <dd>{validation.customer_name ?? "—"}</dd>
            </div>
            <div>
              <dt>明細行</dt>
              <dd className="strong num">
                {validation.rows.toLocaleString("ja-JP")}
              </dd>
            </div>
            <div>
              <dt>契約</dt>
              <dd className="num">{validation.contracts}</dd>
            </div>
            <div>
              <dt>得意先</dt>
              <dd className="num">{validation.clients}</dd>
            </div>
            <div>
              <dt>現場</dt>
              <dd className="num">{validation.sites}</dd>
            </div>
          </dl>

          {errors.length > 0 && (
            <div className="issues error">
              <h3>投入できません</h3>
              <ul>
                {errors.map((i, n) => (
                  <li key={n}>
                    <span className="tag">{i.type}</span>
                    {i.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {others.length > 0 && (
            <div className="issues">
              <h3>確認してください</h3>
              <ul>
                {others.map((i, n) => (
                  <li key={n}>
                    <span className={`tag ${i.severity.toLowerCase()}`}>
                      {SEVERITY_LABEL[i.severity]}
                    </span>
                    {i.message}
                    {i.rows.length > 0 && (
                      <span className="rows">
                        該当行: {i.rows.slice(0, 10).join(", ")}
                        {i.rows.length > 10 && " …"}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {validation.unknown_items.length > 0 && (
            <details className="unknown">
              <summary>
                未登録の品番 {validation.unknown_items.length} 件を表示
              </summary>
              <p className="codes">
                {validation.unknown_items.slice(0, 60).join("、")}
                {validation.unknown_items.length > 60 && " …"}
              </p>
            </details>
          )}

          <div className="actions">
            <button
              className="btn primary"
              disabled={!validation.can_import || phase === "importing"}
              onClick={commit}
            >
              {phase === "importing" ? "取り込んでいます…" : "この内容で取り込む"}
            </button>
            <button className="btn" onClick={reset}>
              やり直す
            </button>
          </div>

          {validation.can_import && validation.existing_lines > 0 && (
            <p className="note warn">
              {validation.period_label} には既に{" "}
              {validation.existing_lines.toLocaleString("ja-JP")} 行あります。
              取り込むとこの月のデータは入れ替わります。他の月には影響しません。
            </p>
          )}
        </section>
      )}

      {result && (
        <section className="result done">
          <h2>取込が完了しました</h2>
          <dl className="facts">
            <div>
              <dt>請求期間</dt>
              <dd className="strong">{result.period_label}</dd>
            </div>
            <div>
              <dt>投入</dt>
              <dd className="strong num">
                {result.inserted_lines.toLocaleString("ja-JP")} 行
              </dd>
            </div>
            <div>
              <dt>入替前の削除</dt>
              <dd className="num">
                {result.deleted_lines.toLocaleString("ja-JP")} 行
              </dd>
            </div>
            <div>
              <dt>受注</dt>
              <dd className="num">{result.orders}</dd>
            </div>
            <div>
              <dt>契約</dt>
              <dd className="num">{result.contracts}</dd>
            </div>
            <div>
              <dt>新規品番</dt>
              <dd className="num">{result.new_items.length}</dd>
            </div>
          </dl>
          <div className="actions">
            <Link href="/periods" className="btn primary">
              請求期間一覧へ
            </Link>
            <button className="btn" onClick={reset}>
              続けて取り込む
            </button>
          </div>
        </section>
      )}
    </main>
  );
}
