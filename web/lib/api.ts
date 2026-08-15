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

// ---------------------------------------------------------------------
// F-04/F-06 明細書・請求書の生成と閲覧
// ---------------------------------------------------------------------

export type TaxCategory = "STANDARD" | "REDUCED";
export type BillingGroupType = "EQUIPMENT" | "COUNTER";

export type GenerateResult = {
  period_id: number;
  invoices: number;
  statements: number;
  assigned_lines: number;
};

export type InvoiceRow = {
  id: number;
  period_id: number;
  customer_id: number;
  customer_name: string;
  tax_category: TaxCategory;
  tax_rate: string | number;
  revision: number;
  status: string;
  statement_count: number;
  total_ex_tax: string | number;
  tax_amount: string | number;
  total_amount: string | number;
};

export type StatementSummaryRow = {
  id: number;
  invoice_id: number;
  contract_id: number;
  contract_no: string;
  client_name: string;
  site_name: string;
  billing_group: BillingGroupType;
  sort_order: number | null;
  line_count: number;
  total_ex_tax: string | number;
  tax_amount: string | number;
  total_amount: string | number;
};

export type PeriodStatementRow = StatementSummaryRow & {
  tax_category: TaxCategory;
  edited_line_count: number;
  is_edited: boolean;
};

export type StatementLine = {
  id: number;
  item_code: string;
  item_name: string;
  delivery_date: string | null;
  quantity: string | number;
  base_charge: string | number | null;
  unit_price: string | number | null;
  duration: string | number | null;
  unit_price_type: "MONTHLY" | "DAILY" | "SALE";
  amount: string | number;
  is_edited: boolean;
  src_quantity: string | number | null;
  src_base_charge: string | number | null;
  src_unit_price: string | number | null;
  src_duration: string | number | null;
};

export type StatementDetail = {
  statement: StatementSummaryRow;
  lines: StatementLine[];
  period_id: number;
  period_label: string;
  period_status: "DRAFT" | "CONFIRMED";
};

export async function generatePeriod(periodId: number): Promise<GenerateResult> {
  const res = await fetch(`${API_BASE}/api/periods/${periodId}/generate`, {
    method: "POST",
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `生成に失敗しました (${res.status})`);
  }
  return res.json();
}

export async function fetchInvoices(periodId: number): Promise<InvoiceRow[]> {
  const res = await fetch(`${API_BASE}/api/periods/${periodId}/invoices`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`請求書の取得に失敗しました (${res.status})`);
  return res.json();
}

export async function fetchInvoiceStatements(invoiceId: number): Promise<StatementSummaryRow[]> {
  const res = await fetch(`${API_BASE}/api/invoices/${invoiceId}/statements`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`明細書の取得に失敗しました (${res.status})`);
  return res.json();
}

export type PeriodStatementFilters = {
  client?: string;
  tax?: TaxCategory | "";
  group?: BillingGroupType | "";
};

export async function fetchPeriodStatements(
  periodId: number,
  filters: PeriodStatementFilters
): Promise<PeriodStatementRow[]> {
  const params = new URLSearchParams();
  if (filters.client) params.set("client", filters.client);
  if (filters.tax) params.set("tax", filters.tax);
  if (filters.group) params.set("group", filters.group);
  const qs = params.toString();
  const res = await fetch(
    `${API_BASE}/api/periods/${periodId}/statements${qs ? `?${qs}` : ""}`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(`明細書一覧の取得に失敗しました (${res.status})`);
  return res.json();
}

export async function fetchStatementDetail(statementId: number): Promise<StatementDetail> {
  const res = await fetch(`${API_BASE}/api/statements/${statementId}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`明細書の取得に失敗しました (${res.status})`);
  return res.json();
}

// ---------------------------------------------------------------------
// F-05 明細手修正 / F-10 修正履歴
// ---------------------------------------------------------------------

export type EditableField = "quantity" | "base_charge" | "unit_price" | "duration";

export type LineEditResult = {
  line: {
    id: number;
    quantity: string | number;
    base_charge: string | number | null;
    unit_price: string | number | null;
    duration: string | number | null;
    unit_price_type: "MONTHLY" | "DAILY" | "SALE";
    amount: string | number;
    is_edited: boolean;
    is_billable: boolean;
  };
  statement: {
    id: number;
    invoice_id: number;
    total_ex_tax: string | number;
    tax_amount: string | number;
    total_amount: string | number;
  } | null;
  invoice: {
    id: number;
    total_ex_tax: string | number;
    tax_amount: string | number;
    total_amount: string | number;
  } | null;
};

export async function patchLine(
  lineId: number,
  // quantity 以外（base_charge/unit_price/duration）は空欄に戻せるため null も許容する。
  // quantity を null にした場合はサーバー側が422で拒否する。
  changes: Partial<Record<EditableField, number | null>>,
  reason?: string
): Promise<LineEditResult> {
  const res = await fetch(`${API_BASE}/api/lines/${lineId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...changes, reason }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `更新に失敗しました (${res.status})`);
  }
  return res.json();
}

export async function resetLine(lineId: number): Promise<LineEditResult> {
  const res = await fetch(`${API_BASE}/api/lines/${lineId}/reset`, { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `取消に失敗しました (${res.status})`);
  }
  return res.json();
}

export type LineHistoryEntry = {
  field: EditableField;
  old_value: string | number | null;
  new_value: string | number | null;
  edited_by_name: string;
  edited_at: string;
  reason: string | null;
};

export async function fetchLineHistory(lineId: number): Promise<LineHistoryEntry[]> {
  const res = await fetch(`${API_BASE}/api/lines/${lineId}/history`, { cache: "no-store" });
  if (!res.ok) throw new Error(`履歴の取得に失敗しました (${res.status})`);
  return res.json();
}

const FIELD_LABEL: Record<EditableField, string> = {
  quantity: "数量",
  base_charge: "基本料",
  unit_price: "単価",
  duration: "日数/月数",
};

export function formatFieldLabel(f: EditableField): string {
  return FIELD_LABEL[f] ?? f;
}
