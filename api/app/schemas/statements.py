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


class StatementDetailOut(BaseModel):
    statement: InvoiceStatementOut
    lines: list[StatementLineOut]
