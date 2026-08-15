"""F-04/F-06 明細書・請求書。

生成トリガーと、生成結果の読み取りAPI。画面（P-04/P-05/P-06）は
フェーズ5でF-05（手修正）と一緒に作る。ここはロジックとAPIまで。

金額はすべて集計クエリで都度算出する。確定（F-08）まで
invoice/invoice_statement の合計列には書き込まない。

消費税は明細書ごと・請求書ごとに2回、独立して切り上げる。
請求書の消費税は「明細書の消費税の合計」ではなく「請求書の税抜合計」
から改めて CEIL する（CLAUDE.md 冒頭のルール）。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.base import BillingGroup, TaxCategory
from app.schemas.statements import (
    GenerateResultOut,
    InvoiceOut,
    InvoiceStatementOut,
    PeriodInvoiceListOut,
    PeriodInvoiceSummaryOut,
    PeriodStatementRowOut,
    StatementDetailOut,
    StatementLineOut,
)
from app.services import generator

router = APIRouter(prefix="/api", tags=["statements"])


@router.post("/periods/{period_id}/generate", response_model=GenerateResultOut)
def generate_period(period_id: int, db: Session = Depends(get_db)) -> GenerateResultOut:
    try:
        summary = generator.generate(db, period_id)
    except generator.PeriodNotFoundError as e:
        db.rollback()
        raise HTTPException(404, str(e)) from e
    except generator.PeriodLockedError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    except generator.NoBillableLinesError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e

    db.commit()
    return GenerateResultOut(
        period_id=summary.period_id,
        invoices=summary.invoices,
        statements=summary.statements,
        assigned_lines=summary.assigned_lines,
    )


# ---------------------------------------------------------------------
# 請求書（税率ごとに1通）
# ---------------------------------------------------------------------

# 請求書の消費税は「請求書自身の税抜合計」から CEIL する。
# 明細書の消費税を合計するのではない（2回、独立して切り上げる）。
_INVOICE_LIST_SQL = text(
    """
    WITH line_totals AS (
        SELECT s.id AS statement_id, s.invoice_id,
               COUNT(bl.id) AS line_count,
               COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) AS total_ex_tax
        FROM invoice_statement s
        LEFT JOIN billing_line bl ON bl.statement_id = s.id AND bl.deleted_at IS NULL
        GROUP BY s.id
    ),
    invoice_totals AS (
        SELECT invoice_id,
               COUNT(*) AS statement_count,
               COALESCE(SUM(total_ex_tax), 0::numeric) AS total_ex_tax
        FROM line_totals
        GROUP BY invoice_id
    )
    SELECT v.id, v.period_id, v.customer_id, cu.name AS customer_name,
           v.tax_category, v.tax_rate, v.revision, v.status,
           COALESCE(it.statement_count, 0) AS statement_count,
           COALESCE(it.total_ex_tax, 0::numeric) AS total_ex_tax,
           CEIL(COALESCE(it.total_ex_tax, 0::numeric) * v.tax_rate) AS tax_amount
    FROM invoice v
    JOIN customer cu ON cu.id = v.customer_id
    LEFT JOIN invoice_totals it ON it.invoice_id = v.id
    WHERE v.period_id = :period_id
    ORDER BY v.tax_category
    """
)


def _invoice_row_to_out(r: dict) -> InvoiceOut:
    total_ex_tax = r["total_ex_tax"]
    tax_amount = r["tax_amount"]
    return InvoiceOut(
        id=r["id"],
        period_id=r["period_id"],
        customer_id=r["customer_id"],
        customer_name=r["customer_name"],
        tax_category=r["tax_category"],
        tax_rate=r["tax_rate"],
        revision=r["revision"],
        status=r["status"],
        statement_count=r["statement_count"],
        total_ex_tax=total_ex_tax,
        tax_amount=tax_amount,
        total_amount=total_ex_tax + tax_amount,
    )


@router.get("/periods/{period_id}/invoices", response_model=PeriodInvoiceListOut)
def list_invoices(period_id: int, db: Session = Depends(get_db)) -> PeriodInvoiceListOut:
    rows = db.execute(_INVOICE_LIST_SQL, {"period_id": period_id}).mappings().all()
    items = [_invoice_row_to_out(dict(r)) for r in rows]
    # 8%・10%の請求書をまたいだ全体合計。フロントで税抜同士を
    # 足し算させない（CLAUDE.md冒頭のルール）ため、ここでDecimalのまま
    # 合算して返す。個々の請求書のCEIL済み消費税を単純合計するだけで、
    # ここで新たにCEILし直すわけではない。
    summary = PeriodInvoiceSummaryOut(
        total_ex_tax=sum((i.total_ex_tax for i in items), Decimal("0")),
        tax_amount=sum((i.tax_amount for i in items), Decimal("0")),
        total_amount=sum((i.total_amount for i in items), Decimal("0")),
    )
    return PeriodInvoiceListOut(items=items, summary=summary)


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)) -> InvoiceOut:
    row = db.execute(
        text(
            """
            WITH line_totals AS (
                SELECT s.id AS statement_id, s.invoice_id,
                       COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) AS total_ex_tax
                FROM invoice_statement s
                LEFT JOIN billing_line bl ON bl.statement_id = s.id AND bl.deleted_at IS NULL
                WHERE s.invoice_id = :invoice_id
                GROUP BY s.id
            )
            SELECT v.id, v.period_id, v.customer_id, cu.name AS customer_name,
                   v.tax_category, v.tax_rate, v.revision, v.status,
                   (SELECT COUNT(*) FROM invoice_statement WHERE invoice_id = v.id) AS statement_count,
                   COALESCE((SELECT SUM(total_ex_tax) FROM line_totals), 0::numeric) AS total_ex_tax,
                   CEIL(COALESCE((SELECT SUM(total_ex_tax) FROM line_totals), 0::numeric) * v.tax_rate) AS tax_amount
            FROM invoice v
            JOIN customer cu ON cu.id = v.customer_id
            WHERE v.id = :invoice_id
            """
        ),
        {"invoice_id": invoice_id},
    ).mappings().one_or_none()

    if row is None:
        raise HTTPException(404, "請求書が見つかりません。")
    return _invoice_row_to_out(dict(row))


# 単体の明細書・請求書の合計だけを取りたいとき用（例: F-05手修正の応答）。
# LIST系のSQLとCEILの式を分けて持つと、片方だけ直して食い違う事故が
# 起きるため、明細書1枚・請求書1件ぶんの計算はここに集約する。
def compute_statement_totals(db: Session, statement_id: int) -> dict | None:
    row = db.execute(
        text(
            """
            SELECT s.id, s.invoice_id,
                   COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) AS total_ex_tax,
                   CEIL(
                       COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) * v.tax_rate
                   ) AS tax_amount
            FROM invoice_statement s
            JOIN invoice v ON v.id = s.invoice_id
            LEFT JOIN billing_line bl ON bl.statement_id = s.id AND bl.deleted_at IS NULL
            WHERE s.id = :statement_id
            GROUP BY s.id, v.tax_rate
            """
        ),
        {"statement_id": statement_id},
    ).mappings().one_or_none()
    if row is None:
        return None
    d = dict(row)
    d["total_amount"] = d["total_ex_tax"] + d["tax_amount"]
    return d


def compute_invoice_totals(db: Session, invoice_id: int) -> dict | None:
    row = db.execute(
        text(
            """
            WITH line_totals AS (
                SELECT s.id AS statement_id,
                       COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) AS total_ex_tax
                FROM invoice_statement s
                LEFT JOIN billing_line bl ON bl.statement_id = s.id AND bl.deleted_at IS NULL
                WHERE s.invoice_id = :invoice_id
                GROUP BY s.id
            )
            SELECT v.id,
                   COALESCE((SELECT SUM(total_ex_tax) FROM line_totals), 0::numeric) AS total_ex_tax,
                   CEIL(
                       COALESCE((SELECT SUM(total_ex_tax) FROM line_totals), 0::numeric) * v.tax_rate
                   ) AS tax_amount
            FROM invoice v
            WHERE v.id = :invoice_id
            """
        ),
        {"invoice_id": invoice_id},
    ).mappings().one_or_none()
    if row is None:
        return None
    d = dict(row)
    d["total_amount"] = d["total_ex_tax"] + d["tax_amount"]
    return d


# ---------------------------------------------------------------------
# 請求明細書（契約 × 請求グループ）
# ---------------------------------------------------------------------

# 明細書の消費税は「その明細書自身の税抜合計」から CEIL する。
_STATEMENT_LIST_SQL = text(
    """
    SELECT s.id, s.invoice_id, s.contract_id, c.contract_no, cl.name AS client_name,
           site.name AS site_name, s.billing_group, s.sort_order,
           COUNT(bl.id) AS line_count,
           COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) AS total_ex_tax,
           CEIL(
               COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) * v.tax_rate
           ) AS tax_amount
    FROM invoice_statement s
    JOIN invoice v ON v.id = s.invoice_id
    JOIN contract c ON c.id = s.contract_id
    JOIN site ON site.id = c.site_id
    JOIN client cl ON cl.id = site.client_id
    LEFT JOIN billing_line bl ON bl.statement_id = s.id AND bl.deleted_at IS NULL
    WHERE s.invoice_id = :invoice_id
    GROUP BY s.id, v.tax_rate, c.contract_no, cl.name, site.name, s.billing_group, s.sort_order
    ORDER BY s.sort_order
    """
)


def _statement_row_to_out(r: dict) -> InvoiceStatementOut:
    total_ex_tax = r["total_ex_tax"]
    tax_amount = r["tax_amount"]
    return InvoiceStatementOut(
        id=r["id"],
        invoice_id=r["invoice_id"],
        # r["contract_id"] を直接参照する（.get で握りつぶさない）。
        # SELECT漏れがあればここで即座にKeyErrorとして表面化する。
        contract_id=r["contract_id"],
        contract_no=r["contract_no"],
        client_name=r["client_name"],
        site_name=r["site_name"],
        billing_group=r["billing_group"],
        sort_order=r["sort_order"],
        line_count=r["line_count"],
        total_ex_tax=total_ex_tax,
        tax_amount=tax_amount,
        total_amount=total_ex_tax + tax_amount,
    )


@router.get("/invoices/{invoice_id}/statements", response_model=list[InvoiceStatementOut])
def list_statements(invoice_id: int, db: Session = Depends(get_db)) -> list[InvoiceStatementOut]:
    rows = db.execute(_STATEMENT_LIST_SQL, {"invoice_id": invoice_id}).mappings().all()
    return [_statement_row_to_out(dict(r)) for r in rows]


# ---------------------------------------------------------------------
# P-04 請求明細書一覧（請求期間内の全請求書を横断）
# ---------------------------------------------------------------------

# format() で組み立ててから text() に渡す（プレースホルダは {client_clause} 等）。
# TextClause を先に作ってから .format しない — 混乱のもとになるため生文字列のまま持つ。
_PERIOD_STATEMENT_LIST_SQL_TEMPLATE = (
    """
    SELECT s.id, s.invoice_id, v.tax_category, s.contract_id, c.contract_no,
           cl.name AS client_name, site.name AS site_name,
           s.billing_group, s.sort_order,
           COUNT(bl.id) AS line_count,
           COUNT(bl.id) FILTER (
               WHERE bl.quantity IS DISTINCT FROM bl.src_quantity
                  OR bl.base_charge IS DISTINCT FROM bl.src_base_charge
                  OR bl.unit_price IS DISTINCT FROM bl.src_unit_price
                  OR bl.duration IS DISTINCT FROM bl.src_duration
           ) AS edited_line_count,
           COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) AS total_ex_tax,
           CEIL(
               COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) * v.tax_rate
           ) AS tax_amount
    FROM invoice_statement s
    JOIN invoice v ON v.id = s.invoice_id
    JOIN contract c ON c.id = s.contract_id
    JOIN site ON site.id = c.site_id
    JOIN client cl ON cl.id = site.client_id
    LEFT JOIN billing_line bl ON bl.statement_id = s.id AND bl.deleted_at IS NULL
    WHERE v.period_id = :period_id
      AND ({client_clause})
      AND ({tax_clause})
      AND ({group_clause})
    GROUP BY s.id, v.id, v.tax_category, v.tax_rate, c.contract_no, cl.name,
             site.name, s.billing_group, s.sort_order
    ORDER BY v.tax_category, s.sort_order
    """
)


@router.get("/periods/{period_id}/statements", response_model=list[PeriodStatementRowOut])
def list_period_statements(
    period_id: int,
    client: str | None = Query(None, description="得意先名（部分一致）"),
    tax: str | None = Query(None, description="STANDARD または REDUCED"),
    group: str | None = Query(None, description="EQUIPMENT または COUNTER"),
    db: Session = Depends(get_db),
) -> list[PeriodStatementRowOut]:
    if tax is not None and tax not in TaxCategory.ALL:
        raise HTTPException(422, f"tax は {TaxCategory.ALL} のいずれかで指定してください。")
    if group is not None and group not in BillingGroup.ALL:
        raise HTTPException(422, f"group は {BillingGroup.ALL} のいずれかで指定してください。")

    bind: dict = {"period_id": period_id}
    clauses = {"client_clause": "TRUE", "tax_clause": "TRUE", "group_clause": "TRUE"}
    if client:
        clauses["client_clause"] = "cl.name ILIKE :client"
        bind["client"] = f"%{client}%"
    if tax:
        clauses["tax_clause"] = "v.tax_category = :tax"
        bind["tax"] = tax
    if group:
        clauses["group_clause"] = "s.billing_group = :group"
        bind["group"] = group

    sql = text(_PERIOD_STATEMENT_LIST_SQL_TEMPLATE.format(**clauses))
    rows = db.execute(sql, bind).mappings().all()

    result = []
    for r in rows:
        d = dict(r)
        result.append(
            PeriodStatementRowOut(
                id=d["id"],
                invoice_id=d["invoice_id"],
                tax_category=d["tax_category"],
                contract_id=d["contract_id"],
                contract_no=d["contract_no"],
                client_name=d["client_name"],
                site_name=d["site_name"],
                billing_group=d["billing_group"],
                sort_order=d["sort_order"],
                line_count=d["line_count"],
                edited_line_count=d["edited_line_count"],
                is_edited=d["edited_line_count"] > 0,
                total_ex_tax=d["total_ex_tax"],
                tax_amount=d["tax_amount"],
                total_amount=d["total_ex_tax"] + d["tax_amount"],
            )
        )
    return result


@router.get("/statements/{statement_id}", response_model=StatementDetailOut)
def get_statement(statement_id: int, db: Session = Depends(get_db)) -> StatementDetailOut:
    header = db.execute(
        text(
            """
            SELECT s.id, s.invoice_id, s.contract_id, c.contract_no,
                   cl.name AS client_name, site.name AS site_name,
                   s.billing_group, s.sort_order,
                   p.id AS period_id, p.status AS period_status,
                   to_char(p.start_date, 'YYYY-MM') AS period_label,
                   COUNT(bl.id) AS line_count,
                   COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) AS total_ex_tax,
                   CEIL(
                       COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) * v.tax_rate
                   ) AS tax_amount
            FROM invoice_statement s
            JOIN invoice v ON v.id = s.invoice_id
            JOIN billing_period p ON p.id = v.period_id
            JOIN contract c ON c.id = s.contract_id
            JOIN site ON site.id = c.site_id
            JOIN client cl ON cl.id = site.client_id
            LEFT JOIN billing_line bl ON bl.statement_id = s.id AND bl.deleted_at IS NULL
            WHERE s.id = :statement_id
            GROUP BY s.id, v.tax_rate, c.contract_no, cl.name, site.name, s.billing_group,
                     s.sort_order, p.id, p.status, p.start_date
            """
        ),
        {"statement_id": statement_id},
    ).mappings().one_or_none()

    if header is None:
        raise HTTPException(404, "請求明細書が見つかりません。")

    lines = db.execute(
        text(
            """
            SELECT bl.id, i.code AS item_code, bl.item_name_snapshot AS item_name,
                   bl.delivery_date, bl.quantity, bl.base_charge, bl.unit_price,
                   bl.duration, bl.unit_price_type, bl.amount,
                   bl.src_quantity, bl.src_base_charge, bl.src_unit_price, bl.src_duration,
                   (
                       bl.quantity IS DISTINCT FROM bl.src_quantity
                       OR bl.base_charge IS DISTINCT FROM bl.src_base_charge
                       OR bl.unit_price IS DISTINCT FROM bl.src_unit_price
                       OR bl.duration IS DISTINCT FROM bl.src_duration
                   ) AS is_edited
            FROM billing_line bl
            JOIN item i ON i.id = bl.item_id
            WHERE bl.statement_id = :statement_id AND bl.deleted_at IS NULL
            ORDER BY bl.delivery_date NULLS LAST, bl.id
            """
        ),
        {"statement_id": statement_id},
    ).mappings().all()

    header_d = dict(header)
    return StatementDetailOut(
        statement=_statement_row_to_out(header_d),
        period_id=header_d["period_id"],
        period_label=header_d["period_label"],
        period_status=header_d["period_status"],
        lines=[
            StatementLineOut(
                id=l["id"],
                item_code=l["item_code"],
                item_name=l["item_name"],
                delivery_date=str(l["delivery_date"]) if l["delivery_date"] else None,
                quantity=l["quantity"],
                base_charge=l["base_charge"],
                unit_price=l["unit_price"],
                duration=l["duration"],
                unit_price_type=l["unit_price_type"],
                amount=l["amount"],
                is_edited=l["is_edited"],
                src_quantity=l["src_quantity"],
                src_base_charge=l["src_base_charge"],
                src_unit_price=l["src_unit_price"],
                src_duration=l["src_duration"],
            )
            for l in lines
        ],
    )
