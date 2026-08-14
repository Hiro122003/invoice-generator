"""F-03 リスト表。

契約を得意先・納品先・契約番号でフィルタし、明細不要（skip_statement）を
設定する画面のAPI。集計はすべて「対象請求期間 かつ 論理削除でない」に
限定する（money-audit の指摘どおり、絞り忘れると他期間や削除行が混入する）。
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Contract
from app.models.base import BillingGroup, TaxCategory
from app.schemas.contracts import (
    BillingLineOut,
    ContractListOut,
    ContractRow,
    ContractSummary,
    ContractToggleOut,
    SkipStatementIn,
)

router = APIRouter(prefix="/api", tags=["contracts"])

# 契約ごとの集計。この請求期間に登場した契約だけを対象にする
# （INNER JOIN のため、他期間にしか登場しない契約は出てこない）。
#
# 絞り込みは billing_line.period_id を直接見る。rental_order.period_id と
# billing_line.period_id は importer.run_import() が同一トランザクション内で
# 同じ period.id から両方を作るため常に一致するが、bl.period_id を使うことで
# 専用の部分インデックス ix_billing_line_period_active（period_id WHERE
# deleted_at IS NULL）が素直に効く。
_STATS_CTE = """
WITH stats AS (
    SELECT
        o.contract_id,
        COUNT(bl.id)                                                    AS line_count,
        COALESCE(SUM(bl.amount) FILTER (WHERE bl.is_billable), 0::numeric) AS total_ex_tax,
        bool_or(i.tax_category = 'REDUCED')                             AS has_reduced,
        bool_or(i.tax_category = 'STANDARD')                            AS has_standard,
        bool_or(i.billing_group = 'COUNTER')                            AS has_counter,
        bool_or(i.billing_group = 'EQUIPMENT')                          AS has_equipment
    FROM billing_line bl
    JOIN rental_order o ON o.id = bl.order_id
    JOIN item i ON i.id = bl.item_id
    WHERE bl.period_id = :period_id AND bl.deleted_at IS NULL
    GROUP BY o.contract_id
)
"""


class Filters:
    def __init__(
        self,
        client: str | None,
        site: str | None,
        contract_no: str | None,
        tax: str | None,
        group: str | None,
        skip_statement: bool | None,
        min_amount: Decimal | None,
        max_amount: Decimal | None,
    ):
        if tax is not None and tax not in TaxCategory.ALL:
            raise HTTPException(422, f"tax は {TaxCategory.ALL} のいずれかで指定してください。")
        if group is not None and group not in BillingGroup.ALL:
            raise HTTPException(422, f"group は {BillingGroup.ALL} のいずれかで指定してください。")

        self.client = client
        self.site = site
        self.contract_no = contract_no
        self.tax = tax
        self.group = group
        self.skip_statement = skip_statement
        self.min_amount = min_amount
        self.max_amount = max_amount

    def where_clause(self) -> tuple[str, dict]:
        """WHERE句を組み立てる。値は必ずバインドパラメータで渡し、文字列連結しない。"""
        clauses: list[str] = []
        bind: dict = {}

        if self.client:
            clauses.append("cl.name ILIKE :client")
            bind["client"] = f"%{self.client}%"
        if self.site:
            clauses.append("s.name ILIKE :site")
            bind["site"] = f"%{self.site}%"
        if self.contract_no:
            clauses.append("c.contract_no ILIKE :contract_no")
            bind["contract_no"] = f"%{self.contract_no}%"
        if self.tax == TaxCategory.REDUCED:
            clauses.append("st.has_reduced")
        elif self.tax == TaxCategory.STANDARD:
            clauses.append("st.has_standard")
        if self.group == BillingGroup.COUNTER:
            clauses.append("st.has_counter")
        elif self.group == BillingGroup.EQUIPMENT:
            clauses.append("st.has_equipment")
        if self.skip_statement is not None:
            clauses.append("c.skip_statement = :skip_statement")
            bind["skip_statement"] = self.skip_statement
        if self.min_amount is not None:
            clauses.append("st.total_ex_tax >= :min_amount")
            bind["min_amount"] = self.min_amount
        if self.max_amount is not None:
            clauses.append("st.total_ex_tax <= :max_amount")
            bind["max_amount"] = self.max_amount

        return (" AND ".join(clauses) if clauses else "TRUE"), bind


def _fetch_rows(db: Session, period_id: int, f: Filters) -> list[dict]:
    where, bind = f.where_clause()
    sql = text(
        _STATS_CTE
        + f"""
        SELECT
            c.id, c.contract_no, cl.name AS client_name, s.name AS site_name,
            s.address, c.skip_statement,
            st.line_count, st.total_ex_tax,
            st.has_reduced, st.has_standard, st.has_counter, st.has_equipment
        FROM contract c
        JOIN site s ON s.id = c.site_id
        JOIN client cl ON cl.id = s.client_id
        JOIN stats st ON st.contract_id = c.id
        WHERE {where}
        ORDER BY c.contract_no
        """
    )
    rows = db.execute(sql, {"period_id": period_id, **bind}).mappings().all()
    return [dict(r) for r in rows]


def _query_filters(
    client: str | None = Query(None, description="得意先名（部分一致）"),
    site: str | None = Query(None, description="納品先名称（部分一致）"),
    contract_no: str | None = Query(None, description="契約番号（部分一致）"),
    tax: str | None = Query(None, description="STANDARD または REDUCED"),
    group: str | None = Query(None, description="EQUIPMENT または COUNTER"),
    skip_statement: bool | None = Query(None, description="明細不要フラグの絞り込み"),
    min_amount: Decimal | None = Query(None, description="税抜金額の下限"),
    max_amount: Decimal | None = Query(None, description="税抜金額の上限"),
) -> Filters:
    return Filters(
        client, site, contract_no, tax, group, skip_statement, min_amount, max_amount
    )


@router.get("/periods/{period_id}/contracts", response_model=ContractListOut)
def list_contracts(
    period_id: int,
    filters: Filters = Depends(_query_filters),
    db: Session = Depends(get_db),
) -> ContractListOut:
    rows = _fetch_rows(db, period_id, filters)
    items = [ContractRow(**r) for r in rows]
    total = sum((r.total_ex_tax for r in items), Decimal("0"))
    return ContractListOut(
        items=items, summary=ContractSummary(count=len(items), total_ex_tax=total)
    )


@router.get("/periods/{period_id}/contracts/export")
def export_contracts_csv(
    period_id: int,
    filters: Filters = Depends(_query_filters),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    rows = _fetch_rows(db, period_id, filters)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["契約番号", "得意先", "納品先名称", "住所", "明細行数", "税抜金額", "明細不要"])
    for r in rows:
        writer.writerow(
            [
                r["contract_no"],
                r["client_name"],
                r["site_name"],
                r["address"] or "",
                r["line_count"],
                r["total_ex_tax"],
                "×" if r["skip_statement"] else "",
            ]
        )

    # Excelでの文字化けを避けるため BOM を付ける
    content = "﻿" + buf.getvalue()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="contracts_{period_id}.csv"'
        },
    )


@router.get(
    "/periods/{period_id}/contracts/{contract_id}/lines",
    response_model=list[BillingLineOut],
)
def list_contract_lines(
    period_id: int, contract_id: int, db: Session = Depends(get_db)
) -> list[BillingLineOut]:
    """ドリルダウン: その契約の明細行一覧。値引行・修正済み行も含めて全件返す。"""
    sql = text(
        """
        SELECT
            bl.id, i.code AS item_code, bl.item_name_snapshot AS item_name,
            i.tax_category, i.billing_group, bl.delivery_date,
            bl.quantity, bl.base_charge, bl.unit_price, bl.duration,
            bl.unit_price_type, bl.amount, bl.is_billable,
            (
                bl.quantity IS DISTINCT FROM bl.src_quantity
                OR bl.base_charge IS DISTINCT FROM bl.src_base_charge
                OR bl.unit_price IS DISTINCT FROM bl.src_unit_price
                OR bl.duration IS DISTINCT FROM bl.src_duration
            ) AS is_edited
        FROM billing_line bl
        JOIN rental_order o ON o.id = bl.order_id
        JOIN item i ON i.id = bl.item_id
        WHERE bl.period_id = :period_id
          AND o.contract_id = :contract_id
          AND bl.deleted_at IS NULL
        ORDER BY bl.delivery_date NULLS LAST, bl.id
        """
    )
    rows = db.execute(
        sql, {"period_id": period_id, "contract_id": contract_id}
    ).mappings().all()
    return [BillingLineOut(**dict(r)) for r in rows]


@router.patch("/contracts/{contract_id}", response_model=ContractToggleOut)
def update_contract(
    contract_id: int, body: SkipStatementIn, db: Session = Depends(get_db)
) -> ContractToggleOut:
    """明細不要フラグの更新。

    洗い替えの対象外のマスタを直接更新するので、値は翌月以降も引き継がれる
    （フェーズ2で実データにより検証済み）。
    """
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(404, "契約が見つかりません。")

    contract.skip_statement = body.skip_statement
    db.commit()
    db.refresh(contract)

    return ContractToggleOut(
        id=contract.id,
        contract_no=contract.contract_no,
        skip_statement=contract.skip_statement,
    )
