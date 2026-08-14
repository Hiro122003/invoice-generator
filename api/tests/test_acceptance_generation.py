"""fixtures/202603.xlsx を取込→生成し、現行VBAと同じ数字が出るか検証する。

期待値の根拠は docs/vba-analysis.md 7章。
参照ファイルはリポジトリに含まれないため、無い環境では skip する。
"""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api.statements import _INVOICE_LIST_SQL, _STATEMENT_LIST_SQL
from app.config import settings
from app.models import AppUser, BillingGroup, Invoice, InvoiceStatement, TaxCategory
from app.models.base import UserRole
from app.services import excel_reader, generator, importer

FIXTURE = Path(settings.fixtures_dir) / "202603.xlsx"

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason=f"参照データがありません: {FIXTURE}",
)

# 現行VBAの出力（docs/vba-analysis.md 7章）
EXPECTED = {
    "invoices": 2,
    "total_standard_ex_tax": Decimal("2583740.00"),
    "total_standard_tax": Decimal("258374"),
    "total_reduced_ex_tax": Decimal("35000.00"),
    "total_reduced_tax": Decimal("2800"),
    "equipment_only_contracts": 76,
    "equipment_and_counter_contracts": 3,
    "counter_only_contracts": 1,
}


@pytest.fixture
def generated(db):
    """参照データを取り込み、明細書・請求書を生成する。"""
    user = AppUser(login_id="pytest-gen", display_name="t", role=UserRole.APPROVER)
    db.add(user)
    db.flush()

    result = excel_reader.parse(str(FIXTURE))
    assert not result.has_error, [i.message for i in result.errors]
    import_summary = importer.run_import(db, result, FIXTURE.name, user)
    db.flush()

    gen_summary = generator.generate(db, import_summary.period_id)
    db.flush()
    return {"period_id": import_summary.period_id, "gen_summary": gen_summary}


class TestInvoiceTotalsMatchVba:
    def test_two_invoices(self, db, generated):
        invoices = db.execute(
            select(Invoice).where(Invoice.period_id == generated["period_id"])
        ).scalars().all()
        assert len(invoices) == EXPECTED["invoices"]
        assert {i.tax_category for i in invoices} == {TaxCategory.STANDARD, TaxCategory.REDUCED}

    def test_standard_invoice_amounts(self, db, generated):
        row = db.execute(
            _INVOICE_LIST_SQL, {"period_id": generated["period_id"]}
        ).mappings().all()
        standard = next(r for r in row if r["tax_category"] == TaxCategory.STANDARD)
        assert standard["total_ex_tax"] == EXPECTED["total_standard_ex_tax"]
        assert standard["tax_amount"] == EXPECTED["total_standard_tax"]

    def test_reduced_invoice_amounts(self, db, generated):
        row = db.execute(
            _INVOICE_LIST_SQL, {"period_id": generated["period_id"]}
        ).mappings().all()
        reduced = next(r for r in row if r["tax_category"] == TaxCategory.REDUCED)
        assert reduced["total_ex_tax"] == EXPECTED["total_reduced_ex_tax"]
        assert reduced["tax_amount"] == EXPECTED["total_reduced_tax"]


class TestBranchCoverageMatchesVba:
    """VBAの分岐①②③（備品のみ／備品＋カウンタ／カウンタのみ）が
    契約単位の請求グループ構成として再現できていること。"""

    def _billing_groups_by_contract(self, db, period_id):
        rows = db.execute(
            select(InvoiceStatement.contract_id, InvoiceStatement.billing_group)
            .join(Invoice, Invoice.id == InvoiceStatement.invoice_id)
            .where(Invoice.period_id == period_id, Invoice.tax_category == TaxCategory.STANDARD)
        ).all()
        groups: dict[int, set[str]] = {}
        for contract_id, group in rows:
            groups.setdefault(contract_id, set()).add(group)
        return groups

    def test_branch_counts(self, db, generated):
        groups = self._billing_groups_by_contract(db, generated["period_id"])

        equipment_only = sum(1 for g in groups.values() if g == {BillingGroup.EQUIPMENT})
        both = sum(
            1 for g in groups.values() if g == {BillingGroup.EQUIPMENT, BillingGroup.COUNTER}
        )
        counter_only = sum(1 for g in groups.values() if g == {BillingGroup.COUNTER})

        assert equipment_only == EXPECTED["equipment_only_contracts"]
        assert both == EXPECTED["equipment_and_counter_contracts"]
        assert counter_only == EXPECTED["counter_only_contracts"]


class TestStatementAmountsSumToInvoice:
    """明細書の税抜合計を足し上げると、必ず請求書の税抜合計に一致する
    （こちらは税抜なので端数が出ない。消費税だけが2回独立して丸められる）。"""

    def test_standard_statements_sum_to_invoice_ex_tax(self, db, generated):
        invoice = db.execute(
            select(Invoice).where(
                Invoice.period_id == generated["period_id"],
                Invoice.tax_category == TaxCategory.STANDARD,
            )
        ).scalar_one()
        stmt_rows = db.execute(
            _STATEMENT_LIST_SQL, {"invoice_id": invoice.id}
        ).mappings().all()
        assert sum(r["total_ex_tax"] for r in stmt_rows) == EXPECTED["total_standard_ex_tax"]

    def test_statement_tax_sum_differs_from_invoice_tax(self, db, generated):
        """明細書ごとの消費税を合計した値は、請求書の消費税と一致しない
        （現行VBAの挙動。CLAUDE.md冒頭のルール）。"""
        invoice = db.execute(
            select(Invoice).where(
                Invoice.period_id == generated["period_id"],
                Invoice.tax_category == TaxCategory.STANDARD,
            )
        ).scalar_one()
        stmt_rows = db.execute(
            _STATEMENT_LIST_SQL, {"invoice_id": invoice.id}
        ).mappings().all()
        sum_of_statement_taxes = sum(r["tax_amount"] for r in stmt_rows)
        assert sum_of_statement_taxes != EXPECTED["total_standard_tax"]
