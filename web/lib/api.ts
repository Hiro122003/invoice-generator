/**
 * API クライアント。
 *
 * サーバーコンポーネントからはコンテナ間ネットワーク（http://api:8000）、
 * ブラウザからはホストのポート（http://localhost:8000）を叩く。
 * 同じ URL では届かないので、実行場所で切り替える。
 */

export const API_BASE =
  typeof window === "undefined"
    ? process.env.API_INTERNAL_BASE ?? "http://api:8000"
    : process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Period = {
  id: number;
  start_date: string;
  end_date: string;
  label: string;
  status: "DRAFT" | "CONFIRMED";
  line_count: number;
  contract_count: number;
  total_ex_tax: string | number | null;
  updated_at: string | null;
};

export type Issue = {
  severity: "ERROR" | "WARNING" | "INFO";
  type: string;
  message: string;
  rows: number[];
};

export type Validation = {
  file_name: string;
  rows: number;
  period_start: string | null;
  period_end: string | null;
  period_label: string | null;
  customer_name: string | null;
  contracts: number;
  clients: number;
  sites: number;
  items: number;
  period_exists: boolean;
  period_status: string | null;
  existing_lines: number;
  unknown_items: string[];
  can_import: boolean;
  issues: Issue[];
};

export type ImportResult = {
  period_id: number;
  period_label: string;
  inserted_lines: number;
  deleted_lines: number;
  orders: number;
  contracts: number;
  clients: number;
  sites: number;
  items: number;
  new_items: string[];
};

/**
 * 金額を桁区切りで表示する。
 *
 * APIは金額を文字列（"2618740.00"）で返す。数値にすると float64 を
 * 経由してしまうため、精度を落とさずに渡すための仕様。
 *
 * この関数は**表示のためだけ**に数値へ変換する。
 * 合計・税額・差額といった計算をフロントで行ってはいけない
 * （必ずサーバー側で Decimal のまま計算した結果を受け取る）。
 */
export function formatYen(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return "—";
  return n.toLocaleString("ja-JP", { maximumFractionDigits: 0 });
}

export function formatDate(value: string | null): string {
  if (!value) return "—";
  return value.slice(0, 10).replace(/-/g, "/");
}

export async function fetchPeriods(): Promise<Period[]> {
  const res = await fetch(`${API_BASE}/api/periods`, { cache: "no-store" });
  if (!res.ok) throw new Error(`請求期間の取得に失敗しました (${res.status})`);
  return res.json();
}
