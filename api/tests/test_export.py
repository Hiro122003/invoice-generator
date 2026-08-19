"""F-09 PDF出力を検証する。

export_period() は実際にPlaywrightでPDFを生成しファイルへ書き出すため、
storage_dir を一時ディレクトリへ差し替えてから実行する（本物の
storage/ を汚さない）。
"""

import datetime as dt
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (
    AppUser,
    BillingLine,
    BillingPeriod,
    Client,
    Contract,
    Customer,
    Invoice,
    IssuedDocument,
    Item,
    Office,
    RentalOrder,
    SalesRep,
    Site,
)
from app.models.base import BillingGroup, TaxCategory, UnitPriceType, UserRole
from app.services import export, generator

MULTI_PERIOD_START = dt.date(2092, 1, 1)
MULTI_PERIOD_END = dt.date(2092, 1, 31)

PERIOD_START = dt.date(2093, 1, 1)
PERIOD_END = dt.date(2093, 1, 31)


@pytest.fixture
def export_graph(db, monkeypatch, tmp_path):
    """本物のstorage/を汚さないよう、書き出し先を一時ディレクトリへ差し替える。"""
    monkeypatch.setattr(export.settings, "storage_dir", str(tmp_path))

    office = Office(code="EXP-OFC", name="テスト営業所")
    customer = Customer(code="EXP-CUST", name="テスト販売先")
    client = Client(name="EXP社", normalized_name="EXP社")
    rep = SalesRep(code="EXP-REP", name="テスト担当")
    db.add_all([office, customer, client, rep])
    db.flush()

    site = Site(name="EXP社 現場1", client_id=client.id)
    db.add(site)
    db.flush()

    contract = Contract(
        contract_no="EXP-A", customer_id=customer.id, site_id=site.id,
        sales_rep_id=rep.id, office_id=office.id,
    )
    item_std = Item(
        code="EXP-STD", name="標準品目",
        tax_category=TaxCategory.STANDARD, billing_group=BillingGroup.EQUIPMENT,
    )
    item_reduced = Item(
        code="EXP-RED", name="軽減税率品目",
        tax_category=TaxCategory.REDUCED, billing_group=BillingGroup.EQUIPMENT,
    )
    period = BillingPeriod(start_date=PERIOD_START, end_date=PERIOD_END)
    db.add_all([contract, item_std, item_reduced, period])
    db.flush()

    order = RentalOrder(period_id=period.id, contract_id=contract.id, order_no="EXP-O1")
    db.add(order)
    db.flush()

    db.add_all([
        BillingLine(
            period_id=period.id, order_id=order.id, item_id=item_std.id,
            item_name_snapshot=item_std.name, quantity=Decimal("2"),
            unit_price=Decimal("1000"), unit_price_type=UnitPriceType.SALE,
            src_quantity=Decimal("2"), src_unit_price=Decimal("1000"),
        ),
        BillingLine(
            period_id=period.id, order_id=order.id, item_id=item_reduced.id,
            item_name_snapshot=item_reduced.name, quantity=Decimal("1"),
            unit_price=Decimal("500"), unit_price_type=UnitPriceType.SALE,
            src_quantity=Decimal("1"), src_unit_price=Decimal("500"),
        ),
    ])
    db.flush()

    user = AppUser(login_id="exp-test", display_name="出力太郎", role=UserRole.APPROVER)
    db.add(user)
    db.flush()

    generator.generate(db, period.id)
    db.flush()

    return {"period": period, "user": user, "tmp_path": tmp_path}


class TestExportPeriod:
    def test_creates_five_documents(self, db, export_graph):
        summary = export.export_period(db, export_graph["period"].id, export_graph["user"])
        db.flush()
        # 請求書2件（10%/8%）×（INVOICE + STATEMENT）+ ZIP1件 = 5
        assert len(summary.files) == 5
        doc_types = sorted(f.doc_type for f in summary.files)
        assert doc_types == ["BUNDLE_ZIP", "INVOICE", "INVOICE", "STATEMENT", "STATEMENT"]

    def test_records_issued_document_rows(self, db, export_graph):
        export.export_period(db, export_graph["period"].id, export_graph["user"])
        db.flush()

        docs = db.execute(
            select(IssuedDocument).where(IssuedDocument.period_id == export_graph["period"].id)
        ).scalars().all()
        assert len(docs) == 5
        for d in docs:
            assert d.issued_by == export_graph["user"].id
            assert d.revision == 1

    def test_files_are_written_and_are_real_pdfs_or_zip(self, db, export_graph):
        summary = export.export_period(db, export_graph["period"].id, export_graph["user"])
        db.flush()

        for f in summary.files:
            path = Path(f.file_path)
            assert path.exists()
            content = path.read_bytes()
            if f.file_name.endswith(".pdf"):
                assert content[:4] == b"%PDF"
            else:
                assert zipfile.is_zipfile(path)

    def test_zip_contains_all_pdf_files(self, db, export_graph):
        summary = export.export_period(db, export_graph["period"].id, export_graph["user"])
        db.flush()

        zip_file = next(f for f in summary.files if f.doc_type == "BUNDLE_ZIP")
        pdf_files = {f.file_name for f in summary.files if f.file_name.endswith(".pdf")}

        with zipfile.ZipFile(zip_file.file_path) as zf:
            assert set(zf.namelist()) == pdf_files

    def test_file_name_includes_period_tax_and_revision(self, db, export_graph):
        summary = export.export_period(db, export_graph["period"].id, export_graph["user"])
        db.flush()

        names = {f.file_name for f in summary.files}
        assert "2093-01_請求書_10%_rev1.pdf" in names
        assert "2093-01_請求書_8%_rev1.pdf" in names
        assert "2093-01_請求明細書_10%_rev1.pdf" in names
        assert "2093-01_請求明細書_8%_rev1.pdf" in names

    def test_no_invoices_raises(self, db, export_graph):
        period2 = BillingPeriod(start_date=dt.date(2093, 2, 1), end_date=dt.date(2093, 2, 28))
        db.add(period2)
        db.flush()
        with pytest.raises(export.NoInvoicesError):
            export.export_period(db, period2.id, export_graph["user"])

    def test_unknown_period_raises(self, db, export_graph):
        with pytest.raises(export.PeriodNotFoundError):
            export.export_period(db, 999_999_999, export_graph["user"])

    def test_regenerating_after_export_does_not_violate_fk(self, db, export_graph):
        """export後にgenerate()し直しても壊れない（回帰テスト）。

        issued_document.invoice_id にON DELETE SET NULLが無かったとき、
        PDF出力済みの期間を再生成しようとすると、generator.generate()の
        洗い替え（DELETE FROM invoice）がFK違反で500になっていた。design.md
        の「試し刷り」（未確定期間でのPDF出力）は、その後も編集・再生成が
        続く前提の運用のため、これは実データ（請求期間23）で実際に発生した
        重大な回帰だった（money-audit確認ずみ）。
        """
        period_id = export_graph["period"].id
        export.export_period(db, period_id, export_graph["user"])
        db.flush()

        # 修正前はここで IntegrityError（FK違反）になっていた。
        generator.generate(db, period_id)
        db.flush()

        # issued_documentは削除されず、invoice_idだけNULLになる
        # （「先方に送ったPDFの記録」はfile_path/file_name/revisionに
        # 残っており、invoice_idが外れても履歴の意味は失われない）。
        docs = db.execute(
            select(IssuedDocument).where(IssuedDocument.period_id == period_id)
        ).scalars().all()
        assert len(docs) == 5
        assert all(d.invoice_id is None for d in docs)
        assert all(d.file_name for d in docs)


@pytest.fixture
def multi_statement_graph(db):
    """1請求書に明細書が2枚（備品・カウンタ）ぶら下がる構成 + 値引行。

    money-audit指摘: _build_invoice_doc の明細書間グルーピング
    （lines_by_statement）が混線しないことを検証するための構成。
    """
    office = Office(code="MST-OFC", name="テスト営業所")
    customer = Customer(code="MST-CUST", name="テスト販売先")
    client = Client(name="MST社", normalized_name="MST社")
    db.add_all([office, customer, client])
    db.flush()

    site = Site(name="MST社 現場1", client_id=client.id)
    db.add(site)
    db.flush()

    contract = Contract(
        contract_no="MST-A", customer_id=customer.id, site_id=site.id, office_id=office.id,
    )
    item_equip = Item(
        code="MST-EQ", name="備品A",
        tax_category=TaxCategory.STANDARD, billing_group=BillingGroup.EQUIPMENT,
    )
    item_counter = Item(
        code="MST-CT", name="カウンタA",
        tax_category=TaxCategory.STANDARD, billing_group=BillingGroup.COUNTER,
    )
    period = BillingPeriod(start_date=MULTI_PERIOD_START, end_date=MULTI_PERIOD_END)
    db.add_all([contract, item_equip, item_counter, period])
    db.flush()

    order = RentalOrder(period_id=period.id, contract_id=contract.id, order_no="MST-O1")
    db.add(order)
    db.flush()

    db.add_all([
        BillingLine(
            period_id=period.id, order_id=order.id, item_id=item_equip.id,
            item_name_snapshot=item_equip.name, quantity=Decimal("1"),
            unit_price=Decimal("1000"), unit_price_type=UnitPriceType.SALE,
            src_quantity=Decimal("1"), src_unit_price=Decimal("1000"),
        ),
        BillingLine(
            period_id=period.id, order_id=order.id, item_id=item_counter.id,
            item_name_snapshot=item_counter.name, quantity=Decimal("1"),
            unit_price=Decimal("300"), unit_price_type=UnitPriceType.SALE,
            src_quantity=Decimal("1"), src_unit_price=Decimal("300"),
        ),
        # 値引行。合計からは除外されるが、明細行としては印字される
        # （P-05画面の既存仕様と同じ。get_statementの行取得SQLも
        # is_billableでフィルタしていない）。
        BillingLine(
            period_id=period.id, order_id=order.id, item_id=item_equip.id,
            item_name_snapshot="値引", quantity=Decimal("1"),
            unit_price=Decimal("-100"), unit_price_type=UnitPriceType.SALE,
            src_quantity=Decimal("1"), src_unit_price=Decimal("-100"),
            is_billable=False,
        ),
    ])
    db.flush()

    user = AppUser(login_id="mst-test", display_name="複数明細太郎", role=UserRole.APPROVER)
    db.add(user)
    db.flush()

    generator.generate(db, period.id)
    db.flush()

    return {"period": period, "user": user, "contract": contract}


class TestMultiStatementGrouping:
    def test_two_statements_do_not_cross_contaminate(self, db, multi_statement_graph):
        invoice = db.execute(
            select(Invoice).where(Invoice.period_id == multi_statement_graph["period"].id)
        ).scalar_one()

        inv_doc, stmt_docs = export._build_invoice_doc(db, invoice)

        assert len(stmt_docs) == 2
        by_group = {s.billing_group: s for s in stmt_docs}
        assert set(by_group) == {"EQUIPMENT", "COUNTER"}

        # 備品の明細書には備品の行だけ、カウンタの明細書にはカウンタの
        # 行だけが入っている（他方の金額が混ざっていない）。値引行
        # （is_billable=false）はgenerator.generate()の時点でどの明細書にも
        # 割り当てられない（statement_idがNULLのまま）ため、そもそも
        # どちらのlinesにも現れない。
        equip = by_group["EQUIPMENT"]
        counter = by_group["COUNTER"]
        assert [ln.item_code for ln in equip.lines] == ["MST-EQ"]
        assert [ln.item_code for ln in counter.lines] == ["MST-CT"]

        # 値引行（-100円）は合計にもそもそも反映されず、税抜1000円のまま。
        assert equip.total_ex_tax == Decimal("1000.00")
        assert counter.total_ex_tax == Decimal("300.00")

        # 請求書全体の合計は両明細書の合計（値引を除く）と一致する。
        assert inv_doc.total_ex_tax == Decimal("1300.00")

    def test_discount_line_is_invisible_in_pdf_not_just_excluded_from_total(
        self, db, multi_statement_graph
    ):
        """値引行はPDFの明細行にも現れない（statement_id自体が割り当たらない）。

        P-05画面（get_statementの行取得SQL）も同じ理由で値引行を表示しない
        （is_billableでのフィルタがあるからではなく、そもそも
        statement_idを持たないため）。
        """
        line = db.execute(
            select(BillingLine).where(BillingLine.item_name_snapshot == "値引")
        ).scalar_one()
        assert line.statement_id is None

        invoice = db.execute(
            select(Invoice).where(Invoice.period_id == multi_statement_graph["period"].id)
        ).scalar_one()
        _inv_doc, stmt_docs = export._build_invoice_doc(db, invoice)

        all_item_codes = {ln.item_code for s in stmt_docs for ln in s.lines}
        assert "値引" not in all_item_codes
        total_lines = sum(len(s.lines) for s in stmt_docs)
        assert total_lines == 2  # 本行2件のみ（値引行は含まれない）
