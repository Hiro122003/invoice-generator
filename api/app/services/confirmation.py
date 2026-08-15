"""F-08 確定・締め。

請求期間を確定してロックする。確定済み期間は洗い替え（再生成・再取込）も
手修正も拒否される（PeriodStatus.CONFIRMED を各所が見ている）。

金額計算はここでは一切行わない。既存の `compute_statement_totals` /
`compute_invoice_totals`（F-05でPATCH /api/lines/{id}用に作った集計）を
そのまま呼び、その結果を invoice / invoice_statement の
スナップショット列へ書き込むだけ。新しい集計式を作らない
（CLAUDE.md「消費税は明細書ごと・請求書ごとに2回、独立してCEIL」に
触れる箇所を増やさないため）。

版数（invoice.revision）は「確定するたびに+1、第1版は1」
（docs/design.md）。`invoice.confirmed_at` が過去に一度でも
設定されていれば「確定→確定解除→再確定」を経ている＝再確定と判定し、
版数を+1する。確定解除では confirmed_at をクリアしない
（次回確定時にこの判定へ使うのと、「最後に確定したのはいつか」の
履歴表示にも使えるため）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.statements import compute_invoice_totals, compute_statement_totals
from app.models import AppUser, BillingPeriod, Invoice, PeriodUnlockLog
from app.models.base import PeriodStatus


class PeriodNotFoundError(Exception):
    pass


class AlreadyConfirmedError(Exception):
    """既に確定済み。確定するには先に確定解除が必要。"""


class NotConfirmedError(Exception):
    """確定済みでないため確定解除できない。"""


class NothingToConfirmError(Exception):
    """請求書が1件も生成されていない。確定する対象がない。"""


@dataclass
class ConfirmSummary:
    period_id: int
    invoices: int
    revision: int
    confirmed_at: datetime


@dataclass
class UnconfirmSummary:
    period_id: int
    from_revision: int


def confirm_period(db: Session, period_id: int, user: AppUser) -> ConfirmSummary:
    """呼び出し側でコミットすること（他の service 関数と同じ規約）。"""
    # generator.generate() / lines.py の手修正と同じ period_id の
    # アドバイザリロックを取ってから状態を読む・書く。ロックなしだと
    # 「読む→この間に手修正や再生成が割り込む→そのままスナップショット
    # を書く」という check-then-act の隙間ができ、確定済み期間の金額が
    # 確定後に動いてしまう事故が実際に起きる（money-auditで再現ずみ）。
    db.execute(text("SELECT pg_advisory_xact_lock(:period_id)"), {"period_id": period_id})

    period = db.get(BillingPeriod, period_id)
    if period is None:
        raise PeriodNotFoundError(f"請求期間が見つかりません: {period_id}")
    if period.status == PeriodStatus.CONFIRMED:
        raise AlreadyConfirmedError(f"{period.label} は既に確定済みです。")

    invoices = db.execute(
        select(Invoice).where(Invoice.period_id == period_id)
    ).scalars().all()
    if not invoices:
        raise NothingToConfirmError(
            "請求書がまだ生成されていません。先に明細書・請求書を生成してください。"
        )

    now = datetime.now(timezone.utc)
    revision = 1
    for invoice in invoices:
        # 過去に一度でも確定していれば「再確定」＝版数を進める。
        if invoice.confirmed_at is not None:
            invoice.revision += 1
        revision = invoice.revision  # 同一期間内の全請求書で揃うはず

        inv_totals = compute_invoice_totals(db, invoice.id)
        # generate() 直後で明細行が1つも割り当てられていない請求書は
        # 実運用では起きない（NoBillableLinesError で弾かれる）が、
        # 念のため None を許容せず落とす（CLAUDE.md「エラーは握りつぶさない」）。
        assert inv_totals is not None
        invoice.total_ex_tax = inv_totals["total_ex_tax"]
        invoice.tax_amount = inv_totals["tax_amount"]
        invoice.total_amount = inv_totals["total_ex_tax"] + inv_totals["tax_amount"]
        invoice.confirmed_at = now
        invoice.confirmed_by = user.id

        for statement in invoice.statements:
            st_totals = compute_statement_totals(db, statement.id)
            assert st_totals is not None
            statement.total_ex_tax = st_totals["total_ex_tax"]
            statement.tax_amount = st_totals["tax_amount"]
            statement.total_amount = st_totals["total_amount"]

    period.status = PeriodStatus.CONFIRMED
    period.confirmed_at = now
    period.confirmed_by = user.id

    return ConfirmSummary(
        period_id=period_id, invoices=len(invoices), revision=revision, confirmed_at=now
    )


def unconfirm_period(
    db: Session, period_id: int, user: AppUser, reason: str
) -> UnconfirmSummary:
    # confirm_period と同じ理由で、状態を読む前にロックする。
    db.execute(text("SELECT pg_advisory_xact_lock(:period_id)"), {"period_id": period_id})

    period = db.get(BillingPeriod, period_id)
    if period is None:
        raise PeriodNotFoundError(f"請求期間が見つかりません: {period_id}")
    if period.status != PeriodStatus.CONFIRMED:
        raise NotConfirmedError(f"{period.label} は確定されていません。")
    reason = reason.strip()
    if not reason:
        # APIレイヤーの UnconfirmIn でも同じ検証をしているが、この関数を
        # 直接呼ぶ経路（テスト等）でも空文字が記録されないよう二重に防ぐ。
        raise ValueError("確定解除の理由を入力してください。")

    invoices = db.execute(
        select(Invoice).where(Invoice.period_id == period_id)
    ).scalars().all()
    from_revision = invoices[0].revision if invoices else 1

    for invoice in invoices:
        # スナップショットをクリアし、確定前と同じ「常に集計クエリで
        # 算出」の状態へ戻す。revision / confirmed_at / confirmed_by は
        # 次回確定時の判定・履歴表示のために残す。
        invoice.total_ex_tax = None
        invoice.tax_amount = None
        invoice.total_amount = None
        for statement in invoice.statements:
            statement.total_ex_tax = None
            statement.tax_amount = None
            statement.total_amount = None

    period.status = PeriodStatus.DRAFT

    db.add(
        PeriodUnlockLog(
            period_id=period_id,
            from_revision=from_revision,
            reason=reason,
            unlocked_by=user.id,
        )
    )

    return UnconfirmSummary(period_id=period_id, from_revision=from_revision)
