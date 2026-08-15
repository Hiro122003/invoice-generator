"""F-04/F-06 明細書・請求書の生成。

VBAの分岐①②③（備品のみ／備品＋カウンタ／カウンタのみ）は、
契約×請求グループでGROUP BYするだけで自然に表現される。この関数が
invoice10.bas / invoice8.bas の1,700行が担っていたことの本体にあたる。

再生成は洗い替え。呼ぶたびに対象請求期間の invoice / invoice_statement を
作り直し、billing_line.statement_id を割り当て直す。金額のスナップ
ショット（total_ex_tax等）は確定時（F-08）まで書き込まない。常に集計
クエリで求める（docs/design.md 5章④）。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.models import BillingLine, BillingPeriod, Invoice, InvoiceStatement
from app.models.base import PeriodStatus, TaxCategory


class PeriodNotFoundError(Exception):
    pass


class PeriodLockedError(Exception):
    """確定済みの請求期間は再生成できない。確定解除が必要。"""


class NoBillableLinesError(Exception):
    """請求対象の明細行が1件もない。"""


@dataclass
class GenerationSummary:
    period_id: int
    invoices: int
    statements: int
    assigned_lines: int


def _wipe_statements_and_invoices(db: Session, period_id: int) -> None:
    """対象請求期間の invoice_statement / invoice だけを作り直す。

    billing_line.statement_id は ON DELETE SET NULL なので、
    invoice_statement を消せば明細行側は自動的に外れる（明細行そのものは
    洗い替えの対象外＝触らない）。
    """
    stmt_ids = (
        select(InvoiceStatement.id)
        .join(Invoice, Invoice.id == InvoiceStatement.invoice_id)
        .where(Invoice.period_id == period_id)
    )
    db.execute(delete(InvoiceStatement).where(InvoiceStatement.id.in_(stmt_ids)))
    db.execute(delete(Invoice).where(Invoice.period_id == period_id))
    db.flush()


# 明細を集める順序。VBAの明細書が「備品→カウンタ」の順にページを積んで
# いたことに合わせる（billing_group の文字コード順だと COUNTER が先に
# 来てしまうため、明示的に優先順位を指定する）。
_GROUP_ORDER = "CASE WHEN i.billing_group = 'EQUIPMENT' THEN 0 ELSE 1 END"


def generate(db: Session, period_id: int) -> GenerationSummary:
    """請求明細書・請求書を生成する。呼び出し側でコミットすること。"""
    period = db.get(BillingPeriod, period_id)
    if period is None:
        raise PeriodNotFoundError(f"請求期間が見つかりません: {period_id}")
    if period.status == PeriodStatus.CONFIRMED:
        raise PeriodLockedError(
            f"{period.label} は確定済みです。生成し直すには確定解除してください。"
        )

    _wipe_statements_and_invoices(db, period_id)

    # 契約番号順・グループ順に取得することで、後段のループがそのまま
    # 明細書の並び順（sort_order）になる。
    #
    # skip_statement が立っている契約は明細書・請求書のどちらにも出さない
    # （「この現場は明細書を出さない」という継続的な取り決め。design.md
    # 「洗い替えの範囲」参照）。この行を対象から外すだけでよく、請求書・
    # 明細書の合計は billing_line.statement_id の割り当てから逆算する
    # ため、他の集計クエリを個別に直す必要はない。
    rows = db.execute(
        text(
            f"""
            SELECT bl.id AS line_id, o.contract_id, c.customer_id,
                   i.tax_category, i.billing_group
            FROM billing_line bl
            JOIN rental_order o ON o.id = bl.order_id
            JOIN contract c ON c.id = o.contract_id
            JOIN item i ON i.id = bl.item_id
            WHERE bl.period_id = :period_id
              AND bl.deleted_at IS NULL
              AND bl.is_billable
              AND NOT c.skip_statement
            ORDER BY c.contract_no, {_GROUP_ORDER}
            """
        ),
        {"period_id": period_id},
    ).mappings().all()

    if not rows:
        raise NoBillableLinesError("請求対象の明細行がありません。")

    invoices: dict[tuple[int, str], Invoice] = {}
    invoice_sort_counters: dict[tuple[int, str], int] = defaultdict(int)
    statements: dict[tuple[tuple[int, str], int, str], InvoiceStatement] = {}
    line_ids_by_statement: dict[int, list[int]] = defaultdict(list)

    for r in rows:
        inv_key = (r["customer_id"], r["tax_category"])
        invoice = invoices.get(inv_key)
        if invoice is None:
            invoice = Invoice(
                period_id=period_id,
                customer_id=r["customer_id"],
                tax_category=r["tax_category"],
                tax_rate=Decimal(TaxCategory.RATE[r["tax_category"]]),
            )
            db.add(invoice)
            db.flush()
            invoices[inv_key] = invoice

        stmt_key = (inv_key, r["contract_id"], r["billing_group"])
        statement = statements.get(stmt_key)
        if statement is None:
            invoice_sort_counters[inv_key] += 1
            statement = InvoiceStatement(
                invoice_id=invoice.id,
                contract_id=r["contract_id"],
                billing_group=r["billing_group"],
                sort_order=invoice_sort_counters[inv_key],
            )
            db.add(statement)
            db.flush()
            statements[stmt_key] = statement

        line_ids_by_statement[statement.id].append(r["line_id"])

    # 明細書ごとにまとめて一括UPDATE（1行ずつ更新しない）
    for statement_id, line_ids in line_ids_by_statement.items():
        db.execute(
            update(BillingLine)
            .where(BillingLine.id.in_(line_ids))
            .values(statement_id=statement_id)
        )
    db.flush()

    return GenerationSummary(
        period_id=period_id,
        invoices=len(invoices),
        statements=len(statements),
        assigned_lines=sum(len(v) for v in line_ids_by_statement.values()),
    )
