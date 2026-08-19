"""F-07 発行前チェック。"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class ValidationIssueOut(BaseModel):
    category: str
    severity: str
    message: str
    contract_id: int | None
    contract_no: str | None
    client_name: str | None
    site_name: str | None
    item_code: str | None
    item_name: str | None
    amount: Decimal | None
    previous_amount: Decimal | None


class ValidationSummaryOut(BaseModel):
    high: int
    medium: int
    info: int


class ValidationResultOut(BaseModel):
    period_id: int
    previous_period_id: int | None
    previous_period_label: str | None
    summary: ValidationSummaryOut
    issues: list[ValidationIssueOut]
