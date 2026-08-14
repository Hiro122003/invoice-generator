"""リスト表（F-03）APIの入出力。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel


class ContractRow(BaseModel):
    """リスト表の1行。契約 × その請求期間の集計。"""

    id: int
    contract_no: str
    client_name: str
    site_name: str
    address: str | None
    skip_statement: bool

    line_count: int  # 論理削除でない明細行数（値引含む）
    total_ex_tax: Decimal  # 請求対象（is_billable）の税抜合計
    has_reduced: bool  # 8%対象の行を含むか
    has_standard: bool  # 10%対象の行を含むか
    has_counter: bool  # カウンタ類を含むか
    has_equipment: bool  # 備品類を含むか


class ContractSummary(BaseModel):
    """絞り込み結果の集計。常時表示するため一覧と一緒に返す。"""

    count: int
    total_ex_tax: Decimal


class ContractListOut(BaseModel):
    items: list[ContractRow]
    summary: ContractSummary


class SkipStatementIn(BaseModel):
    skip_statement: bool


class ContractToggleOut(BaseModel):
    """PATCH /api/contracts/{id} の応答。

    リスト表の他の集計列（明細行数・税抜金額など）は請求期間に
    紐づくため、契約単体の更新エンドポイントでは返さない。
    フロントは skip_statement だけを画面上の行にマージする。
    """

    id: int
    contract_no: str
    skip_statement: bool


class BillingLineOut(BaseModel):
    """ドリルダウン: 契約の明細行1件。"""

    id: int
    item_code: str
    item_name: str
    tax_category: str
    billing_group: str
    delivery_date: dt.date | None
    quantity: Decimal
    base_charge: Decimal | None
    unit_price: Decimal | None
    duration: Decimal | None
    unit_price_type: str
    amount: Decimal
    is_billable: bool
    is_edited: bool
