"""F-04/F-06 明細書・請求書APIの入出力。"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class GenerateResultOut(BaseModel):
    period_id: int
    invoices: int
    statements: int
    assigned_lines: int


class InvoiceOut(BaseModel):
    """請求書。金額は集計クエリで都度算出する（確定するまで保存しない）。"""

    id: int
    period_id: int
    customer_id: int
    customer_name: str
    tax_category: str
    tax_rate: Decimal
    revision: int
    status: str
    statement_count: int
    total_ex_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal


class PeriodInvoiceSummaryOut(BaseModel):
    """P-06 請求書一覧の全体合計（8%・10%の請求書をまたいだ合計）。

    フロントで税抜同士を足し算しない（CLAUDE.md冒頭のルール）ための
    バックエンド側の合算値。個々の請求書のCEIL済み消費税をそのまま
    合計するだけで、ここで新たにCEILし直すわけではない。
    """

    total_ex_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal


class PeriodInvoiceListOut(BaseModel):
    items: list[InvoiceOut]
    summary: PeriodInvoiceSummaryOut


class InvoiceStatementOut(BaseModel):
    """請求明細書。1件 = 契約 × 請求グループ。"""

    id: int
    invoice_id: int
    contract_id: int
    contract_no: str
    client_name: str
    site_name: str
    billing_group: str
    sort_order: int | None
    line_count: int
    total_ex_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal


class StatementLineOut(BaseModel):
    id: int
    item_code: str
    item_name: str
    delivery_date: str | None
    quantity: Decimal
    base_charge: Decimal | None
    unit_price: Decimal | None
    duration: Decimal | None
    unit_price_type: str
    amount: Decimal
    is_edited: bool
    # 手修正セルのホバー表示（「元: 3」）用。取込時の値をそのまま返す
    src_quantity: Decimal | None
    src_base_charge: Decimal | None
    src_unit_price: Decimal | None
    src_duration: Decimal | None


class StatementDetailOut(BaseModel):
    statement: InvoiceStatementOut
    lines: list[StatementLineOut]
    # 確定済み期間かどうかを画面側で判定するために持たせる。
    # 全セル読み取り専用にする・ロックアイコンを出す、の分岐に使う。
    period_id: int
    period_label: str
    period_status: str


class PeriodStatementRowOut(BaseModel):
    """P-04 請求明細書一覧の1行。請求期間内の全請求書を横断する。

    is_edited は edited_line_count > 0 から変換時に設定する
    （Pydanticのフィールドはプロパティではなく実値として持たせる）。
    """

    id: int
    invoice_id: int
    tax_category: str
    contract_id: int
    contract_no: str
    client_name: str
    site_name: str
    billing_group: str
    sort_order: int | None
    line_count: int
    edited_line_count: int
    is_edited: bool
    total_ex_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal
