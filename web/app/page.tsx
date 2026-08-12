/**
 * フェーズ1の疎通確認ページ。
 * 3層（web → api → db）が繋がっていることだけを確認する。
 * 業務画面はフェーズ2以降で作る。
 */

type DbHealth = {
  status: string;
  postgres: string;
  tables: { expected: number; existing: number; missing: string[] };
};

async function fetchDbHealth(): Promise<DbHealth | { error: string }> {
  const base = process.env.API_INTERNAL_BASE ?? "http://api:8000";
  try {
    const res = await fetch(`${base}/api/health/db`, { cache: "no-store" });
    if (!res.ok) return { error: `API が ${res.status} を返しました` };
    return (await res.json()) as DbHealth;
  } catch {
    return { error: "API に接続できません" };
  }
}

export default async function Home() {
  const health = await fetchDbHealth();
  const ok = !("error" in health);
  const allTablesReady =
    ok && health.tables.existing === health.tables.expected;

  return (
    <main>
      <h1>請求書生成システム</h1>
      <p className="lede">
        フェーズ1（基盤構築）。3層の疎通のみを確認しています。
      </p>

      <section>
        <h2>接続状態</h2>
        <dl>
          <div className="row">
            <dt>Next.js</dt>
            <dd>
              <span className="chip ok">起動中</span>
            </dd>
          </div>
          <div className="row">
            <dt>FastAPI</dt>
            <dd>
              <span className={`chip ${ok ? "ok" : "ng"}`}>
                {ok ? "応答あり" : "応答なし"}
              </span>
            </dd>
          </div>
          <div className="row">
            <dt>PostgreSQL</dt>
            <dd>
              {ok ? (
                <>
                  <span className="chip ok">接続済み</span>
                  <span className="detail">{health.postgres}</span>
                </>
              ) : (
                <span className="chip ng">未確認</span>
              )}
            </dd>
          </div>
          <div className="row">
            <dt>テーブル</dt>
            <dd>
              {ok ? (
                <>
                  <span className={`chip ${allTablesReady ? "ok" : "warn"}`}>
                    {health.tables.existing} / {health.tables.expected}
                  </span>
                  {!allTablesReady && (
                    <span className="detail">
                      未作成: {health.tables.missing.join(", ") || "—"}
                    </span>
                  )}
                </>
              ) : (
                <span className="chip ng">未確認</span>
              )}
            </dd>
          </div>
        </dl>

        {!ok && (
          <p className="err">
            {health.error}。<code>docker compose logs api</code> で原因を確認してください。
          </p>
        )}
        {ok && !allTablesReady && (
          <p className="err">
            マイグレーションが未適用です。
            <code>docker compose exec api alembic upgrade head</code> を実行してください。
          </p>
        )}
      </section>
    </main>
  );
}
