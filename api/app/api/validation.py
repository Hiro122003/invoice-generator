"""F-07 発行前チェック（P-07）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.validation import (
    ValidationIssueOut,
    ValidationResultOut,
    ValidationSummaryOut,
)
from app.services import validation
from app.services.validation import Severity

router = APIRouter(prefix="/api", tags=["validation"])


@router.get("/periods/{period_id}/validate", response_model=ValidationResultOut)
def validate_period(period_id: int, db: Session = Depends(get_db)) -> ValidationResultOut:
    try:
        result = validation.validate_period(db, period_id)
    except validation.PeriodNotFoundError as e:
        db.rollback()
        raise HTTPException(404, str(e)) from e

    # 読み取り専用だが、アドバイザリロックを取ったトランザクションを
    # 開いたままにしない。commit してもデータは変わらない（SELECTのみ）。
    db.commit()

    # フィールドを明示的に列挙する。**vars(i) だと、将来
    # ValidationIssue にフィールドを追加してこちらを更新し忘れても
    # Pydantic（既定でextra="ignore"）が黙って無視してしまい、
    # 気づけないまま値が欠けたレスポンスを返し続ける（money-audit指摘）。
    issues = [
        ValidationIssueOut(
            category=i.category,
            severity=i.severity,
            message=i.message,
            contract_id=i.contract_id,
            contract_no=i.contract_no,
            client_name=i.client_name,
            site_name=i.site_name,
            item_code=i.item_code,
            item_name=i.item_name,
            amount=i.amount,
            previous_amount=i.previous_amount,
        )
        for i in result.issues
    ]
    summary = ValidationSummaryOut(
        high=sum(1 for i in result.issues if i.severity == Severity.HIGH),
        medium=sum(1 for i in result.issues if i.severity == Severity.MEDIUM),
        info=sum(1 for i in result.issues if i.severity == Severity.INFO),
    )
    return ValidationResultOut(
        period_id=result.period_id,
        previous_period_id=result.previous_period_id,
        previous_period_label=result.previous_period_label,
        summary=summary,
        issues=issues,
    )
