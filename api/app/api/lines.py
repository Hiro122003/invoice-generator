"""F-05 明細手修正 / F-10 修正履歴。

編集できるのは数量・基本料・単価・日数/月数の4項目だけ。金額
（billing_line.amount）は生成列なので直接は書き込めず、常に4項目から
自動計算される。「数量×単価と金額が食い違う明細書」を物理的に
作れない状態を維持する（CLAUDE.md 冒頭のルール）。

PATCH は1リクエストで明細行・明細書・請求書の3階層の金額をまとめて
返す（docs/design.md 9章）。画面側は応答をそのまま反映すればよく、
再取得は不要。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.statements import compute_invoice_totals, compute_statement_totals
from app.db import get_db
from app.models import AppUser, BillingLine, BillingPeriod, LineEditLog
from app.models.base import PeriodStatus
from app.schemas.lines import (
    InvoiceTotalsOut,
    LineEditIn,
    LineEditResultOut,
    LineHistoryEntryOut,
    LineOut,
    StatementTotalsOut,
)

router = APIRouter(prefix="/api", tags=["lines"])

EDITABLE_FIELDS = ("quantity", "base_charge", "unit_price", "duration")


def _line_out(line: BillingLine) -> LineOut:
    return LineOut(
        id=line.id,
        quantity=line.quantity,
        base_charge=line.base_charge,
        unit_price=line.unit_price,
        duration=line.duration,
        unit_price_type=line.unit_price_type,
        amount=line.amount,
        is_edited=line.is_edited,
        is_billable=line.is_billable,
    )


def _build_result(db: Session, line: BillingLine) -> LineEditResultOut:
    """明細行が明細書に割り当て済み（statement_id あり）なら、
    その明細書・請求書の合計も一緒に計算して返す。未生成なら None。
    """
    statement_out = None
    invoice_out = None
    if line.statement_id is not None:
        s = compute_statement_totals(db, line.statement_id)
        if s is not None:
            statement_out = StatementTotalsOut(**s)
            i = compute_invoice_totals(db, s["invoice_id"])
            if i is not None:
                invoice_out = InvoiceTotalsOut(**i)
    return LineEditResultOut(line=_line_out(line), statement=statement_out, invoice=invoice_out)


def _get_line_or_404(db: Session, line_id: int) -> BillingLine:
    line = db.get(BillingLine, line_id)
    if line is None:
        raise HTTPException(404, "明細行が見つかりません。")
    return line


def _check_not_locked(db: Session, line: BillingLine) -> None:
    # generator.generate() / confirmation.confirm_period 等と同じ
    # period_id のアドバイザリロックを取ってから確定状態を読む。
    # ロックなしだと「読む→この間に確定される→そのまま書き込む」という
    # check-then-act の隙間に確定処理が割り込み、確定済み期間の金額が
    # 確定後に動いてしまう事故が実際に起きる（money-auditで再現ずみ）。
    db.execute(
        text("SELECT pg_advisory_xact_lock(:period_id)"), {"period_id": line.period_id}
    )
    period = db.get(BillingPeriod, line.period_id)
    if period is not None and period.status == PeriodStatus.CONFIRMED:
        raise HTTPException(
            409,
            f"{period.label} は確定済みのため編集できません。確定解除してください。",
        )


def _apply_changes(
    db: Session, line: BillingLine, changes: dict, user: AppUser, reason: str | None
) -> None:
    """変更を明細行に反映し、実際に値が変わった項目だけ履歴に残す。

    値が同じ変更（再送や無効なPATCH）は記録しない。ログを水増ししない。
    """
    for field, new_value in changes.items():
        old_value = getattr(line, field)
        if old_value == new_value:
            continue
        db.add(
            LineEditLog(
                line_id=line.id,
                field=field,
                old_value=old_value,
                new_value=new_value,
                edited_by=user.id,
                reason=reason,
            )
        )
        setattr(line, field, new_value)


@router.patch("/lines/{line_id}", response_model=LineEditResultOut)
def update_line(
    line_id: int,
    body: LineEditIn,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
) -> LineEditResultOut:
    line = _get_line_or_404(db, line_id)
    _check_not_locked(db, line)

    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise HTTPException(422, f"編集できない項目です: {', '.join(sorted(unknown))}")
    if "quantity" in changes and changes["quantity"] is None:
        raise HTTPException(422, "数量は空にできません。")

    _apply_changes(db, line, changes, user, body.reason)

    db.flush()
    db.refresh(line)  # amount は生成列なのでDBから読み直す
    result = _build_result(db, line)
    db.commit()
    return result


@router.post("/lines/{line_id}/reset", response_model=LineEditResultOut)
def reset_line(
    line_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
) -> LineEditResultOut:
    """その行だけを取込時の値（src_*）に戻す。"""
    line = _get_line_or_404(db, line_id)
    _check_not_locked(db, line)

    src_values = {
        "quantity": line.src_quantity,
        "base_charge": line.src_base_charge,
        "unit_price": line.src_unit_price,
        "duration": line.src_duration,
    }
    # 現状 src_quantity が NULL になる経路はない（取込時に必ず数値が入る）が、
    # update_line と同じ防御を揃えておく。将来「行を追加する」機能等で
    # src_quantity が未設定のまま reset を呼べる経路ができても壊れない。
    if src_values["quantity"] is None:
        raise HTTPException(500, "取込時の数量が記録されていないため戻せません。")
    _apply_changes(db, line, src_values, user, "取込時の値に戻す")

    db.flush()
    db.refresh(line)
    result = _build_result(db, line)
    db.commit()
    return result


@router.get("/lines/{line_id}/history", response_model=list[LineHistoryEntryOut])
def get_line_history(line_id: int, db: Session = Depends(get_db)) -> list[LineHistoryEntryOut]:
    _get_line_or_404(db, line_id)
    logs = db.execute(
        select(LineEditLog, AppUser.display_name)
        .join(AppUser, AppUser.id == LineEditLog.edited_by)
        .where(LineEditLog.line_id == line_id)
        .order_by(LineEditLog.edited_at.desc())
    ).all()
    return [
        LineHistoryEntryOut(
            field=log.field,
            old_value=log.old_value,
            new_value=log.new_value,
            edited_by_name=name,
            edited_at=log.edited_at,
            reason=log.reason,
        )
        for log, name in logs
    ]
