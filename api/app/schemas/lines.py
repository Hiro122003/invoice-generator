"""F-05 明細手修正 / F-10 修正履歴 のAPI入出力。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel


class LineEditIn(BaseModel):
    """PATCH /api/lines/{id} のリクエスト。

    送らなかった項目は変更しない（Noneではなく「未指定」として扱う。
    exclude_unset で判定するため、bodyに含めなければ何も起きない）。
    """

    quantity: Decimal | None = None
    base_charge: Decimal | None = None
    unit_price: Decimal | None = None
    duration: Decimal | None = None
    reason: str | None = None


class LineOut(BaseModel):
    id: int
    quantity: Decimal
    base_charge: Decimal | None
    unit_price: Decimal | None
    duration: Decimal | None
    unit_price_type: str
    amount: Decimal
    is_edited: bool
    is_billable: bool


class StatementTotalsOut(BaseModel):
    id: int
    invoice_id: int
    total_ex_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal


class InvoiceTotalsOut(BaseModel):
    id: int
    total_ex_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal


class LineEditResultOut(BaseModel):
    """1リクエストで明細行・明細書・請求書の3階層をまとめて返す（design.md 9章）。

    明細書・請求書がまだ生成されていない（statement_id が NULL）場合は
    None になる。
    """

    line: LineOut
    statement: StatementTotalsOut | None
    invoice: InvoiceTotalsOut | None


class LineHistoryEntryOut(BaseModel):
    field: str
    old_value: Decimal | None
    new_value: Decimal | None
    edited_by_name: str
    edited_at: dt.datetime
    reason: str | None
