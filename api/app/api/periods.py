"""請求期間の一覧・確定/確定解除（F-08）。取込の起点になる画面（P-01）のデータ源。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import AppUser
from app.schemas.confirmation import (
    ConfirmResultOut,
    UnconfirmIn,
    UnconfirmResultOut,
)
from app.schemas.imports import PeriodOut
from app.services import confirmation

router = APIRouter(prefix="/api/periods", tags=["periods"])

# 件数と金額を1本で取る。明細行を都度ロードすると期間数×行数になるため生SQLで集計する。
# 集計は必ず「請求対象・論理削除でない・対象期間」の3条件で絞る。
_LIST_SQL = text(
    """
    SELECT p.id,
           p.start_date,
           p.end_date,
           p.status,
           p.updated_at,
           p.confirmed_at,
           COALESCE(agg.line_count, 0)     AS line_count,
           COALESCE(agg.contract_count, 0) AS contract_count,
           agg.total_ex_tax
      FROM billing_period p
      LEFT JOIN (
            SELECT bl.period_id,
                   COUNT(*)                         AS line_count,
                   COUNT(DISTINCT o.contract_id)    AS contract_count,
                   SUM(bl.amount) FILTER (WHERE bl.is_billable) AS total_ex_tax
              FROM billing_line bl
              JOIN rental_order o ON o.id = bl.order_id
             WHERE bl.deleted_at IS NULL
             GROUP BY bl.period_id
           ) agg ON agg.period_id = p.id
     ORDER BY p.start_date DESC
    """
)


@router.get("", response_model=list[PeriodOut])
def list_periods(db: Session = Depends(get_db)) -> list[PeriodOut]:
    rows = db.execute(_LIST_SQL).mappings().all()
    return [
        PeriodOut(
            id=r["id"],
            start_date=r["start_date"],
            end_date=r["end_date"],
            label=r["start_date"].strftime("%Y-%m"),
            status=r["status"],
            line_count=r["line_count"],
            contract_count=r["contract_count"],
            total_ex_tax=r["total_ex_tax"],
            updated_at=r["updated_at"],
            confirmed_at=r["confirmed_at"],
        )
        for r in rows
    ]


@router.get("/{period_id}", response_model=PeriodOut)
def get_period(period_id: int, db: Session = Depends(get_db)) -> PeriodOut:
    rows = db.execute(_LIST_SQL).mappings().all()
    for r in rows:
        if r["id"] == period_id:
            return PeriodOut(
                id=r["id"],
                start_date=r["start_date"],
                end_date=r["end_date"],
                label=r["start_date"].strftime("%Y-%m"),
                status=r["status"],
                line_count=r["line_count"],
                contract_count=r["contract_count"],
                total_ex_tax=r["total_ex_tax"],
                updated_at=r["updated_at"],
                confirmed_at=r["confirmed_at"],
            )
    raise HTTPException(status_code=404, detail="請求期間が見つかりません。")


@router.post("/{period_id}/confirm", response_model=ConfirmResultOut)
def confirm_period(
    period_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
) -> ConfirmResultOut:
    try:
        summary = confirmation.confirm_period(db, period_id, user)
    except confirmation.PeriodNotFoundError as e:
        db.rollback()
        raise HTTPException(404, str(e)) from e
    except confirmation.AlreadyConfirmedError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    except confirmation.NothingToConfirmError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e

    db.commit()
    return ConfirmResultOut(
        period_id=summary.period_id,
        invoices=summary.invoices,
        revision=summary.revision,
        confirmed_at=summary.confirmed_at,
    )


@router.post("/{period_id}/unconfirm", response_model=UnconfirmResultOut)
def unconfirm_period(
    period_id: int,
    body: UnconfirmIn,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
) -> UnconfirmResultOut:
    try:
        summary = confirmation.unconfirm_period(db, period_id, user, body.reason)
    except confirmation.PeriodNotFoundError as e:
        db.rollback()
        raise HTTPException(404, str(e)) from e
    except confirmation.NotConfirmedError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e

    db.commit()
    return UnconfirmResultOut(
        period_id=summary.period_id, from_revision=summary.from_revision
    )
