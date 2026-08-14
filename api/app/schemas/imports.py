"""取込APIの入出力。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class IssueOut(BaseModel):
    severity: str
    type: str
    message: str
    rows: list[int] = []


class ValidationOut(BaseModel):
    """検証結果。DBは変更していない。"""

    file_name: str
    rows: int
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    period_label: str | None = None
    customer_name: str | None = None

    contracts: int = 0
    clients: int = 0
    sites: int = 0
    items: int = 0

    # 既存の請求期間と重なる場合の情報
    period_exists: bool = False
    period_status: str | None = None
    existing_lines: int = 0

    unknown_items: list[str] = []
    can_import: bool = False
    issues: list[IssueOut] = []


class ImportResultOut(BaseModel):
    """投入結果。"""

    model_config = ConfigDict(from_attributes=True)

    period_id: int
    period_label: str
    inserted_lines: int
    deleted_lines: int
    deleted_in_source: int = 0
    orders: int
    contracts: int
    clients: int
    sites: int
    items: int
    new_items: list[str] = []


class PeriodOut(BaseModel):
    """請求期間の一覧項目。

    total_ex_tax は Decimal。Pydantic はこれをJSONでは文字列
    （"2618740.00"）として出力する。数値にすると受け取り側の
    float64 を経由してしまうため、精度を保つにはこれが正しい。
    フロントは表示に使うだけで、金額の計算はしない。
    """

    id: int
    start_date: dt.date
    end_date: dt.date
    label: str
    status: str
    line_count: int
    contract_count: int
    total_ex_tax: Decimal | None = None
    updated_at: dt.datetime | None = None
