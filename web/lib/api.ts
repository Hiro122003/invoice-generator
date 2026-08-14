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

/**
 * ブラウザが直接開くURL（<a href> やダウンドードリンクなど）専用。
 *
 * API_BASE は実行環境（サーバー/ブラウザ）で値が変わるため、
 * "use client" コンポーネントの render 内で href を組み立てると、
 * サーバー側の初回レンダリング（SSR）ではコンテナ内部URLが焼き込まれ、
 * ブラウザでの再描画時に公開URLへ変わってハイドレーション不整合になる。
 *
 * href は「ブラウザが後で開く文字列」でしかなく、SSR中にfetchするわけ
 * ではないので、常に公開URLで統一してよい。
 */
export const PUBLIC_API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

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

// ---------------------------------------------------------------------
// F-03 リスト表
// ---------------------------------------------------------------------

export type ContractRow = {
  id: number;
  contract_no: string;
  client_name: string;
  site_name: string;
  address: string | null;
  skip_statement: boolean;
  line_count: number;
  total_ex_tax: string | number;
  has_reduced: boolean;
  has_standard: boolean;
  has_counter: boolean;
  has_equipment: boolean;
};

export type ContractSummary = {
  count: number;
  total_ex_tax: string | number;
};

export type ContractListResponse = {
  items: ContractRow[];
  summary: ContractSummary;
};

export type BillingLineRow = {
  id: number;
  item_code: string;
  item_name: string;
  tax_category: "STANDARD" | "REDUCED";
  billing_group: "EQUIPMENT" | "COUNTER";
  delivery_date: string | null;
  quantity: string | number;
  base_charge: string | number | null;
  unit_price: string | number | null;
  duration: string | number | null;
  unit_price_type: "MONTHLY" | "DAILY" | "SALE";
  amount: string | number;
  is_billable: boolean;
  is_edited: boolean;
};

export type ContractFilters = {
  client?: string;
  site?: string;
  contract_no?: string;
  tax?: "STANDARD" | "REDUCED" | "";
  group?: "EQUIPMENT" | "COUNTER" | "";
  skip_statement?: "" | "true" | "false";
  min_amount?: string;
  max_amount?: string;
};

export function buildContractQuery(filters: ContractFilters): string {
  const params = new URLSearchParams();
  if (filters.client) params.set("client", filters.client);
  if (filters.site) params.set("site", filters.site);
  if (filters.contract_no) params.set("contract_no", filters.contract_no);
  if (filters.tax) params.set("tax", filters.tax);
  if (filters.group) params.set("group", filters.group);
  if (filters.skip_statement) params.set("skip_statement", filters.skip_statement);
  if (filters.min_amount) params.set("min_amount", filters.min_amount);
  if (filters.max_amount) params.set("max_amount", filters.max_amount);
  return params.toString();
}

export async function fetchContracts(
  periodId: number,
  filters: ContractFilters
): Promise<ContractListResponse> {
  const qs = buildContractQuery(filters);
  const res = await fetch(
    `${API_BASE}/api/periods/${periodId}/contracts${qs ? `?${qs}` : ""}`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(`契約一覧の取得に失敗しました (${res.status})`);
  return res.json();
}

export async function fetchContractLines(
  periodId: number,
  contractId: number
): Promise<BillingLineRow[]> {
  const res = await fetch(
    `${API_BASE}/api/periods/${periodId}/contracts/${contractId}/lines`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(`明細行の取得に失敗しました (${res.status})`);
  return res.json();
}

export async function updateSkipStatement(
  contractId: number,
  skipStatement: boolean
): Promise<{ id: number; contract_no: string; skip_statement: boolean }> {
  const res = await fetch(`${API_BASE}/api/contracts/${contractId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skip_statement: skipStatement }),
  });
  if (!res.ok) throw new Error(`更新に失敗しました (${res.status})`);
  return res.json();
}

const UNIT_PRICE_TYPE_LABEL: Record<BillingLineRow["unit_price_type"], string> = {
  MONTHLY: "月単価",
  DAILY: "日単価",
  SALE: "販売単価",
};

export function formatUnitPriceType(t: BillingLineRow["unit_price_type"]): string {
  return UNIT_PRICE_TYPE_LABEL[t] ?? t;
}
