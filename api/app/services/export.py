"""F-09 PDF出力。明細書・請求書をPDF化し、issued_documentへ記録する。

design.md「状態遷移」表のとおり、確定済みでなくてもPDF出力自体は可能
（未確定は「試し刷り」、確定済みは「正式」という運用上の違いだけで、
アプリ側で出力を禁止してはいない）。金額は常にその時点の
compute_invoice_totals（F-05で作った集計）と _STATEMENT_LIST_SQL
（明細書一覧の既存SQL）を使う。確定済み期間はこれらの値が確定時の
スナップショットと一致する（生成列・手修正がどちらもロックされている
ため。CLAUDE.md参照）。

未確定期間はPATCHで手修正が同時に走りうるため、export_period()の冒頭で
generator.generate()/confirmation.py/lines.pyと同じ
pg_advisory_xact_lock(period_id) を取ってから読む。ロックなしだと、
1回の出力の中で「請求書ヘッダの合計」「明細書サマリ行」「明細書自身の
ページ」がそれぞれ別のSQL文（＝別スナップショット）を見るため、
export実行中に手修正が割り込むと同一PDF内で数字が食い違う
（money-auditで指摘・再現）。

発行済みPDFは削除しない。洗い替えの対象外（docs/design.md）。
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.statements import _STATEMENT_LIST_SQL, compute_invoice_totals
from app.config import settings
from app.models import AppUser, BillingPeriod, Invoice, IssuedDocument
from app.models.base import DocType
from app.services.pdf import render_pdfs
from app.services.pdf_templates import (
    InvoiceDoc,
    InvoiceSummaryRow,
    StatementDoc,
    StatementLineDoc,
    TAX_LABEL,
    render_invoice_document_html,
    render_statement_document_html,
)


class PeriodNotFoundError(Exception):
    pass


class NoInvoicesError(Exception):
    """請求書が1件も生成されていない。先に生成が必要。"""


@dataclass
class IssuedFile:
    doc_type: str
    invoice_id: int | None
    revision: int
    file_name: str
    file_path: str
    byte_size: int


@dataclass
class ExportSummary:
    period_id: int
    files: list[IssuedFile]


# 明細書1枚ぶんの明細行を、明細書ヘッダごとまとめて取る。
# statement_id → StatementDoc の組み立てに使う。
_STATEMENT_LINES_SQL = text(
    """
    SELECT bl.statement_id, bl.id AS line_id, i.code AS item_code,
           bl.item_name_snapshot AS item_name, bl.delivery_date,
           bl.quantity, bl.base_charge, bl.unit_price, bl.duration,
           bl.unit_price_type, bl.amount
    FROM billing_line bl
    JOIN item i ON i.id = bl.item_id
    WHERE bl.statement_id = ANY(:statement_ids) AND bl.deleted_at IS NULL
    ORDER BY bl.statement_id, bl.delivery_date NULLS LAST, bl.id
    """
)


def _build_invoice_doc(db: Session, invoice: Invoice) -> tuple[InvoiceDoc, list[StatementDoc]]:
    inv_totals = compute_invoice_totals(db, invoice.id)
    assert inv_totals is not None  # 呼び出し側で「請求書が1件もない」は弾いている

    stmt_rows = db.execute(_STATEMENT_LIST_SQL, {"invoice_id": invoice.id}).mappings().all()

    summary_rows = [
        InvoiceSummaryRow(
            contract_no=r["contract_no"],
            site_name=r["site_name"],
            billing_group=r["billing_group"],
            total_ex_tax=r["total_ex_tax"],
            total_amount=r["total_ex_tax"] + r["tax_amount"],
        )
        for r in stmt_rows
    ]
    invoice_doc = InvoiceDoc(
        id=invoice.id,
        # customer_name は呼び出し側（export_period）が一括取得して
        # 上書きする（N+1回避のため、ここでは空のまま作る）。
        customer_name="",
        tax_category=invoice.tax_category,
        tax_rate=invoice.tax_rate,
        revision=invoice.revision,
        total_ex_tax=inv_totals["total_ex_tax"],
        tax_amount=inv_totals["tax_amount"],
        total_amount=inv_totals["total_amount"],
        rows=summary_rows,
    )

    statement_ids = [r["id"] for r in stmt_rows]
    lines_by_statement: dict[int, list[StatementLineDoc]] = {sid: [] for sid in statement_ids}
    if statement_ids:
        line_rows = db.execute(
            _STATEMENT_LINES_SQL, {"statement_ids": statement_ids}
        ).mappings().all()
        for lr in line_rows:
            lines_by_statement[lr["statement_id"]].append(
                StatementLineDoc(
                    delivery_date=str(lr["delivery_date"]) if lr["delivery_date"] else None,
                    item_code=lr["item_code"],
                    item_name=lr["item_name"],
                    quantity=lr["quantity"],
                    base_charge=lr["base_charge"],
                    unit_price=lr["unit_price"],
                    duration=lr["duration"],
                    unit_price_type=lr["unit_price_type"],
                    amount=lr["amount"],
                )
            )

    # _STATEMENT_LIST_SQL が明細書ごとの税抜・消費税をすでに返しているため、
    # compute_statement_totals をもう一度呼ばない。同じ値を二重に取りに
    # 行くと往復が増えるだけでなく、読み直す分だけレース窓（他リクエストの
    # 手修正が割り込める隙）が広がる（money-audit指摘）。
    statement_docs = [
        StatementDoc(
            id=r["id"],
            contract_no=r["contract_no"],
            client_name=r["client_name"],
            site_name=r["site_name"],
            billing_group=r["billing_group"],
            total_ex_tax=r["total_ex_tax"],
            tax_amount=r["tax_amount"],
            total_amount=r["total_ex_tax"] + r["tax_amount"],
            lines=lines_by_statement.get(r["id"], []),
        )
        for r in stmt_rows
    ]

    return invoice_doc, statement_docs


def _file_name(period_label: str, doc_label: str, tax_category: str, revision: int) -> str:
    tax_label = TAX_LABEL.get(tax_category, tax_category)
    return f"{period_label}_{doc_label}_{tax_label}_rev{revision}.pdf"


def export_period(db: Session, period_id: int, user: AppUser) -> ExportSummary:
    """呼び出し側でコミットすること（他の service 関数と同じ規約）。"""
    # generator.generate() / confirmation.py / lines.py と同じロックキー。
    # 未確定期間はexport中もPATCHでの手修正が同時に走りうるため、
    # このトランザクションが完走するまで対象期間への書き込みを待たせる。
    db.execute(text("SELECT pg_advisory_xact_lock(:period_id)"), {"period_id": period_id})

    period = db.get(BillingPeriod, period_id)
    if period is None:
        raise PeriodNotFoundError(f"請求期間が見つかりません: {period_id}")

    invoices = db.execute(
        select(Invoice).where(Invoice.period_id == period_id).order_by(Invoice.tax_category)
    ).scalars().all()
    if not invoices:
        raise NoInvoicesError(
            "請求書がまだ生成されていません。先に明細書・請求書を生成してください。"
        )

    period_label = period.label
    out_dir = Path(settings.storage_dir) / period_label
    out_dir.mkdir(parents=True, exist_ok=True)

    # 顧客名（customer.name）はInvoiceに直接持たせていないため、
    # 生SQLで一括取得しておく（N+1回避）。
    customer_names = dict(
        db.execute(
            text(
                "SELECT v.id, c.name FROM invoice v JOIN customer c ON c.id = v.customer_id"
                " WHERE v.period_id = :period_id"
            ),
            {"period_id": period_id},
        ).all()
    )

    invoice_docs: list[InvoiceDoc] = []
    statement_doc_lists: list[list[StatementDoc]] = []
    for invoice in invoices:
        inv_doc, stmt_docs = _build_invoice_doc(db, invoice)
        inv_doc.customer_name = customer_names.get(invoice.id, "")
        invoice_docs.append(inv_doc)
        statement_doc_lists.append(stmt_docs)

    # PDF化はブラウザ起動コストが高いので、このエクスポート1回ぶんの
    # HTMLをまとめて1つのPlaywrightセッションに渡す。
    html_jobs: list[tuple[str, str, int]] = []  # (doc_type, html, invoice_index)
    for idx, inv_doc in enumerate(invoice_docs):
        html_jobs.append(("INVOICE", render_invoice_document_html(period_label, inv_doc), idx))
        html_jobs.append(
            (
                "STATEMENT",
                render_statement_document_html(period_label, statement_doc_lists[idx]),
                idx,
            )
        )

    pdf_bytes_list = render_pdfs([h for _, h, _ in html_jobs])

    files: list[IssuedFile] = []
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for (doc_type, _html, idx), pdf_bytes in zip(html_jobs, pdf_bytes_list):
            invoice = invoices[idx]
            inv_doc = invoice_docs[idx]
            doc_label = "請求明細書" if doc_type == "STATEMENT" else "請求書"
            file_name = _file_name(period_label, doc_label, invoice.tax_category, inv_doc.revision)
            file_path = out_dir / file_name
            file_path.write_bytes(pdf_bytes)
            zf.writestr(file_name, pdf_bytes)

            db.add(
                IssuedDocument(
                    period_id=period_id,
                    invoice_id=invoice.id,
                    doc_type=doc_type,
                    revision=inv_doc.revision,
                    file_path=str(file_path),
                    file_name=file_name,
                    byte_size=len(pdf_bytes),
                    issued_by=user.id,
                )
            )
            files.append(
                IssuedFile(
                    doc_type=doc_type,
                    invoice_id=invoice.id,
                    revision=inv_doc.revision,
                    file_name=file_name,
                    file_path=str(file_path),
                    byte_size=len(pdf_bytes),
                )
            )

    zip_revision = max((inv.revision for inv in invoice_docs), default=1)
    zip_name = f"{period_label}_一括_rev{zip_revision}.zip"
    zip_path = out_dir / zip_name
    zip_bytes = zip_buffer.getvalue()
    zip_path.write_bytes(zip_bytes)

    db.add(
        IssuedDocument(
            period_id=period_id,
            invoice_id=None,
            doc_type=DocType.BUNDLE_ZIP,
            revision=zip_revision,
            file_path=str(zip_path),
            file_name=zip_name,
            byte_size=len(zip_bytes),
            issued_by=user.id,
        )
    )
    files.append(
        IssuedFile(
            doc_type=DocType.BUNDLE_ZIP,
            invoice_id=None,
            revision=zip_revision,
            file_name=zip_name,
            file_path=str(zip_path),
            byte_size=len(zip_bytes),
        )
    )

    return ExportSummary(period_id=period_id, files=files)
