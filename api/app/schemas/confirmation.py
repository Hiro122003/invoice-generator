"""F-08 確定・締めAPIの入出力。"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, field_validator


class ConfirmResultOut(BaseModel):
    """確定の結果。請求書ごとの版数は揃うはずなので代表値を1つ返す。"""

    period_id: int
    invoices: int
    revision: int
    confirmed_at: dt.datetime


class UnconfirmIn(BaseModel):
    """確定解除。理由は必須（design.md「確定解除は理由の入力を必須とする」）。"""

    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("確定解除の理由を入力してください。")
        return v


class UnconfirmResultOut(BaseModel):
    period_id: int
    from_revision: int
