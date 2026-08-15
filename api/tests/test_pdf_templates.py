"""F-09 PDFのHTML組み立てを検証する（Playwrightは使わない）。

金額の文字列化・ページ構成（明細書ごとに1ページ）が正しいかを、
HTML文字列に対する検証で確認する。実際のPDF化（レイアウト崩れ等）は
test_export.pyでPlaywrightを通して確認する。
"""

from decimal import Decimal

from app.services.pdf_templates import (
    InvoiceDoc,
    InvoiceSummaryRow,
    StatementDoc,
    StatementLineDoc,
    _yen,
    render_invoice_document_html,
    render_statement_document_html,
)


def _line(**overrides) -> StatementLineDoc:
    base = dict(
        delivery_date="2025-03-01",
        item_code="DM-001",
        item_name="テスト品目",
        quantity=Decimal("3"),
        base_charge=None,
        unit_price=Decimal("33"),
        duration=Decimal("31"),
        unit_price_type="DAILY",
        amount=Decimal("3069.00"),
    )
    base.update(overrides)
    return StatementLineDoc(**base)


class TestYenRounding:
    """money-audit指摘: 円未満の端数（F-05手修正で単価に小数を入れると
    起こりうる）で、PDF側と画面側（web/lib/api.ts formatYen）の丸め方向
    がずれないことを確認する。"""

    def test_half_rounds_up_not_banker_rounding(self):
        # ROUND_HALF_EVEN（銀行丸め）なら1234.50→1234（4は偶数なので
        # 切り捨て）になってしまう。画面側のtoLocaleStringは半分を
        # 切り上げるため、"1,235"に揃える。
        assert _yen(Decimal("1234.50")) == "1,235"

    def test_half_rounds_up_odd_base_too(self):
        # ROUND_HALF_EVENなら1235.50→1236（6は偶数）で偶然一致してしまう
        # ケース。奇数側（1236.50→1237）も確認して丸め方式そのものを検証する。
        assert _yen(Decimal("1236.50")) == "1,237"

    def test_whole_yen_unaffected(self):
        assert _yen(Decimal("2583740.00")) == "2,583,740"

    def test_none_is_dash(self):
        assert _yen(None) == "—"


class TestStatementDocumentHtml:
    def test_includes_header_and_amounts(self):
        st = StatementDoc(
            id=1, contract_no="7000000000001", client_name="架空建設株式会社",
            site_name="架空建設株式会社 第01現場", billing_group="EQUIPMENT",
            total_ex_tax=Decimal("16976.00"), tax_amount=Decimal("1698"),
            total_amount=Decimal("18674.00"), lines=[_line()],
        )
        html = render_statement_document_html("2025-03", [st])

        assert "架空建設株式会社" in html
        assert "7000000000001" in html
        assert "16,976" in html
        assert "1,698" in html
        assert "18,674" in html
        assert "DM-001" in html

    def test_multiple_statements_become_multiple_pages(self):
        docs = [
            StatementDoc(
                id=i, contract_no=f"C{i}", client_name="X社", site_name="現場",
                billing_group="EQUIPMENT", total_ex_tax=Decimal("100.00"),
                tax_amount=Decimal("10"), total_amount=Decimal("110.00"), lines=[],
            )
            for i in range(3)
        ]
        html = render_statement_document_html("2025-03", docs)
        assert html.count('class="doc"') == 3

    def test_null_base_charge_and_duration_render_as_dash(self):
        st = StatementDoc(
            id=1, contract_no="C1", client_name="X社", site_name="現場",
            billing_group="EQUIPMENT", total_ex_tax=Decimal("100.00"),
            tax_amount=Decimal("10"), total_amount=Decimal("110.00"),
            lines=[_line(base_charge=None, duration=None, unit_price_type="SALE")],
        )
        html = render_statement_document_html("2025-03", [st])
        assert "—" in html


class TestInvoiceDocumentHtml:
    def test_includes_customer_and_totals(self):
        inv = InvoiceDoc(
            id=1, customer_name="架空事務機株式会社", tax_category="STANDARD",
            tax_rate=Decimal("0.100"), revision=1,
            total_ex_tax=Decimal("2583740.00"), tax_amount=Decimal("258374"),
            total_amount=Decimal("2842114.00"),
            rows=[
                InvoiceSummaryRow(
                    contract_no="7000000000001", site_name="現場1",
                    billing_group="EQUIPMENT", total_ex_tax=Decimal("16976.00"),
                    total_amount=Decimal("18674.00"),
                )
            ],
        )
        html = render_invoice_document_html("2025-03", inv)

        assert "架空事務機株式会社" in html
        assert "10%" in html
        assert "第1版" in html
        assert "2,583,740" in html
        assert "258,374" in html
        assert "2,842,114" in html
        assert "7000000000001" in html

    def test_reduced_tax_shows_8_percent(self):
        inv = InvoiceDoc(
            id=2, customer_name="X社", tax_category="REDUCED",
            tax_rate=Decimal("0.080"), revision=1,
            total_ex_tax=Decimal("35000.00"), tax_amount=Decimal("2800"),
            total_amount=Decimal("37800.00"), rows=[],
        )
        html = render_invoice_document_html("2025-03", inv)
        assert "8%" in html
