"""F-04/F-06 明細書・請求書生成のロジックを検証する。

VBAの分岐①②③（備品のみ／備品＋カウンタ／カウンタのみ）が
GROUP BY contract_id, billing_group に置き換わったことを確認する。
自作データセットで境界条件を、実データ（fixtures）で全体の数値を検証する。
"""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AppUser,
    BillingLine,
    BillingPeriod,
    Client,
    Contract,
    Customer,
    Invoice,
    InvoiceStatement,
    Item,
    Office,
    RentalOrder,
    SalesRep,
    Site,
)
from app.models.base import BillingGroup, PeriodStatus, TaxCategory, UnitPriceType, UserRole
from app.services import generator

PERIOD_START = dt.date(2097, 1, 1)
PERIOD_END = dt.date(2097, 1, 31)


@pytest.fixture
def branch_graph(db: Session) -> dict:
    """VBAの3分岐を再現する最小データセット。

    契約X: 備品のみ                → 分岐①
    契約Y: 備品＋カウンタ           → 分岐②（明細書2枚）
    契約Z: カウンタのみ             → 分岐③
    契約W: 8%（ＳＲ－ＷＰＥＴ相当） → 別の請求書（税区分が違う）
    """
    office = Office(code="GEN-OFC", name="テスト営業所")
    customer = Customer(code="GEN-CUST", name="テスト販売先")
    client = Client(name="G社", normalized_name="G社")
    rep = SalesRep(code="GEN-REP", name="テスト担当")
    db.add_all([office, customer, client, rep])
    db.flush()

    sites = {
        k: Site(name=f"G社 現場{k}", address=f"住所{k}", client_id=client.id)
        for k in "XYZW"
    }
    db.add_all(sites.values())
    db.flush()

    contracts = {
        k: Contract(
            contract_no=f"GEN-{k}", customer_id=customer.id, site_id=sites[k].id,
            sales_rep_id=rep.id, office_id=office.id,
        )
        for k in "XYZW"
    }
    db.add_all(contracts.values())

    item_equip = Item(code="GEN-EQUIP", name="備品", tax_category=TaxCategory.STANDARD, billing_group=BillingGroup.EQUIPMENT)
    item_counter = Item(code="GEN-COUNTER", name="カウンタ", tax_category=TaxCategory.STANDARD, billing_group=BillingGroup.COUNTER)
    item_reduced = Item(code="GEN-REDUCED", name="軽減税率品", tax_category=TaxCategory.REDUCED, billing_group=BillingGroup.EQUIPMENT)
    period = BillingPeriod(start_date=PERIOD_START, end_date=PERIOD_END)
    db.add_all([item_equip, item_counter, item_reduced, period])
    db.flush()

    orders = {
        k: RentalOrder(period_id=period.id, contract_id=contracts[k].id, order_no=f"GEN-O{k}")
        for k in "XYZW"
    }
    db.add_all(orders.values())
    db.flush()

    def line(order, item, quantity, unit_price):
        return BillingLine(
            period_id=period.id, order_id=order.id, item_id=item.id,
            item_name_snapshot=item.name, quantity=Decimal(quantity),
            unit_price=Decimal(unit_price), duration=None,
            unit_price_type=UnitPriceType.SALE,
            src_quantity=Decimal(quantity), src_unit_price=Decimal(unit_price),
        )

    db.add_all([
        line(orders["X"], item_equip, 1, 1000),           # 分岐①: 備品のみ
        line(orders["Y"], item_equip, 1, 2000),            # 分岐②: 備品
        line(orders["Y"], item_counter, 10, 30),            # 分岐②: カウンタ 300
        line(orders["Z"], item_counter, 5, 40),              # 分岐③: カウンタのみ 200
        line(orders["W"], item_reduced, 1, 500),             # 8%（別の請求書）
    ])
    db.flush()

    user = AppUser(login_id="gen-test", display_name="t", role=UserRole.APPROVER)
    db.add(user)
    db.flush()

    return {"period": period, "contracts": contracts, "user": user}


class TestBranchesBecomeGroupBy:
    """VBAの分岐①②③が GROUP BY contract_id, billing_group で表現できること。"""

    def test_branch1_equipment_only_gets_one_statement(self, db, branch_graph):
        summary = generator.generate(db, branch_graph["period"].id)
        stmts = db.execute(
            select(InvoiceStatement).where(
                InvoiceStatement.contract_id == branch_graph["contracts"]["X"].id
            )
        ).scalars().all()
        assert len(stmts) == 1
        assert stmts[0].billing_group == BillingGroup.EQUIPMENT

    def test_branch2_equipment_and_counter_gets_two_statements(self, db, branch_graph):
        generator.generate(db, branch_graph["period"].id)
        stmts = db.execute(
            select(InvoiceStatement).where(
                InvoiceStatement.contract_id == branch_graph["contracts"]["Y"].id
            )
        ).scalars().all()
        assert {s.billing_group for s in stmts} == {BillingGroup.EQUIPMENT, BillingGroup.COUNTER}

    def test_branch3_counter_only_gets_one_statement(self, db, branch_graph):
        generator.generate(db, branch_graph["period"].id)
        stmts = db.execute(
            select(InvoiceStatement).where(
                InvoiceStatement.contract_id == branch_graph["contracts"]["Z"].id
            )
        ).scalars().all()
        assert len(stmts) == 1
        assert stmts[0].billing_group == BillingGroup.COUNTER

    def test_different_tax_category_gets_separate_invoice(self, db, branch_graph):
        """8%品目を含む契約Wは、10%とは別の請求書に属する。"""
        generator.generate(db, branch_graph["period"].id)
        invoices = db.execute(
            select(Invoice).where(Invoice.period_id == branch_graph["period"].id)
        ).scalars().all()
        assert {i.tax_category for i in invoices} == {TaxCategory.STANDARD, TaxCategory.REDUCED}
        assert len(invoices) == 2

    def test_summary_counts(self, db, branch_graph):
        # 統計: 4請求書統計→ ①1明細書 + ②2明細書 + ③1明細書 = 4（10%） + 1（8%） = 5
        summary = generator.generate(db, branch_graph["period"].id)
        assert summary.invoices == 2
        assert summary.statements == 5
        assert summary.assigned_lines == 5


@pytest.fixture
def rounding_graph(db: Session) -> dict:
    """税抜101円の契約を2つ作る。個別に切り上げると合計が変わる例。

        明細書ごと: CEIL(101*0.1)=11 が2件 → 合計22
        請求書として: CEIL(202*0.1)=21           ← 一致しない
    """
    office = Office(code="RND-OFC", name="テスト営業所")
    customer = Customer(code="RND-CUST", name="テスト販売先")
    client = Client(name="R社", normalized_name="R社")
    db.add_all([office, customer, client])
    db.flush()

    period = BillingPeriod(start_date=dt.date(2096, 1, 1), end_date=dt.date(2096, 1, 31))
    item = Item(code="RND-ITEM", name="端数品", tax_category=TaxCategory.STANDARD, billing_group=BillingGroup.EQUIPMENT)
    db.add_all([period, item])
    db.flush()

    contracts = []
    for k in "AB":
        site = Site(name=f"R社 現場{k}", client_id=client.id)
        db.add(site)
        db.flush()
        contract = Contract(contract_no=f"RND-{k}", customer_id=customer.id, site_id=site.id, office_id=office.id)
        db.add(contract)
        db.flush()
        order = RentalOrder(period_id=period.id, contract_id=contract.id, order_no=f"RND-O{k}")
        db.add(order)
        db.flush()
        db.add(BillingLine(
            period_id=period.id, order_id=order.id, item_id=item.id,
            item_name_snapshot=item.name, quantity=Decimal("1"), unit_price=Decimal("101"),
            unit_price_type=UnitPriceType.SALE, src_quantity=Decimal("1"), src_unit_price=Decimal("101"),
        ))
        contracts.append(contract)
    db.flush()

    return {"period": period, "contracts": contracts}


class TestIndependentRounding:
    """消費税は明細書ごと・請求書ごとに2回、独立して切り上げる。"""

    def test_statement_tax_is_independently_ceiled(self, db, rounding_graph):
        generator.generate(db, rounding_graph["period"].id)
        stmts = db.execute(
            select(InvoiceStatement)
            .join(Invoice, Invoice.id == InvoiceStatement.invoice_id)
            .where(Invoice.period_id == rounding_graph["period"].id)
        ).scalars().all()
        assert len(stmts) == 2

    def test_invoice_tax_not_equal_to_sum_of_statement_taxes(self, db, rounding_graph):
        """請求書の消費税は、明細書の消費税を合計した値と一致しないことがある。

        Σ(明細書ごとの消費税) = CEIL(101*0.1)*2 = 11*2 = 22
        請求書の消費税（正しい計算） = CEIL(202*0.1) = 21   ← 22ではない

        api/app/api/statements.py の実際のSQL（_STATEMENT_LIST_SQL /
        _INVOICE_LIST_SQL）を直接呼び、システムが本当にこの値を返すことを
        確認する（Pythonで並行計算した値との突き合わせではない）。
        """
        from app.api.statements import _INVOICE_LIST_SQL, _STATEMENT_LIST_SQL

        generator.generate(db, rounding_graph["period"].id)
        invoice = db.execute(
            select(Invoice).where(Invoice.period_id == rounding_graph["period"].id)
        ).scalar_one()

        stmt_rows = db.execute(
            _STATEMENT_LIST_SQL, {"invoice_id": invoice.id}
        ).mappings().all()
        assert len(stmt_rows) == 2
        assert all(r["tax_amount"] == Decimal("11") for r in stmt_rows)
        sum_of_statement_taxes = sum(r["tax_amount"] for r in stmt_rows)
        assert sum_of_statement_taxes == Decimal("22")

        invoice_row = db.execute(
            _INVOICE_LIST_SQL, {"period_id": rounding_graph["period"].id}
        ).mappings().one()
        assert invoice_row["total_ex_tax"] == Decimal("202.00")
        assert invoice_row["tax_amount"] == Decimal("21")

        # 「明細書の消費税を合計する」実装だったらここで一致してしまう。
        # 一致しないことが正しい設計（CLAUDE.md冒頭のルール）。
        assert sum_of_statement_taxes != invoice_row["tax_amount"]


class TestRegenerationIsIdempotent:
    """再生成（洗い替え）で二重に明細書が作られないこと。"""

    def test_regenerate_does_not_duplicate(self, db, branch_graph):
        generator.generate(db, branch_graph["period"].id)
        summary2 = generator.generate(db, branch_graph["period"].id)
        assert summary2.statements == 5

        total_statements = db.execute(
            select(InvoiceStatement)
            .join(Invoice, Invoice.id == InvoiceStatement.invoice_id)
            .where(Invoice.period_id == branch_graph["period"].id)
        ).scalars().all()
        assert len(total_statements) == 5

    def test_regenerate_reassigns_lines_after_edit(self, db, branch_graph):
        """明細を追加してから再生成すると、新しい構成に更新される。"""
        generator.generate(db, branch_graph["period"].id)

        # 契約Xにカウンタ品目を追加 → 分岐①→②に変わるはず
        item_counter = db.execute(
            select(Item).where(Item.code == "GEN-COUNTER")
        ).scalar_one()
        order_x = db.execute(
            select(RentalOrder).where(RentalOrder.contract_id == branch_graph["contracts"]["X"].id)
        ).scalar_one()
        db.add(BillingLine(
            period_id=branch_graph["period"].id, order_id=order_x.id, item_id=item_counter.id,
            item_name_snapshot="カウンタ", quantity=Decimal("1"), unit_price=Decimal("100"),
            unit_price_type=UnitPriceType.SALE, src_quantity=Decimal("1"), src_unit_price=Decimal("100"),
        ))
        db.flush()

        generator.generate(db, branch_graph["period"].id)
        stmts = db.execute(
            select(InvoiceStatement).where(
                InvoiceStatement.contract_id == branch_graph["contracts"]["X"].id
            )
        ).scalars().all()
        assert {s.billing_group for s in stmts} == {BillingGroup.EQUIPMENT, BillingGroup.COUNTER}


class TestStatementListApi:
    """api/app/api/statements.py の一覧SQLが返す列の妥当性。

    money-auditの指摘: _STATEMENT_LIST_SQL が s.contract_id を SELECT
    しておらず、_statement_row_to_out 側の r.get("contract_id", 0) が
    エラーを握りつぶして常に 0 を返していた（CLAUDE.md「エラーは
    握りつぶさない」に反する）。金額には影響しないが、フェーズ5で
    「明細書一覧→契約IDで明細取得」という画面を作ると誤った契約を
    指す経路になるため、ここで固定する。
    """

    def test_statement_list_returns_real_contract_id(self, db, branch_graph):
        from app.api.statements import _STATEMENT_LIST_SQL

        generator.generate(db, branch_graph["period"].id)
        invoice = db.execute(
            select(Invoice).where(
                Invoice.period_id == branch_graph["period"].id,
                Invoice.tax_category == TaxCategory.STANDARD,
            )
        ).scalar_one()

        rows = db.execute(
            _STATEMENT_LIST_SQL, {"invoice_id": invoice.id}
        ).mappings().all()
        assert rows, "明細書が1件も返らない"

        expected_ids = {
            branch_graph["contracts"]["X"].id,
            branch_graph["contracts"]["Y"].id,
            branch_graph["contracts"]["Z"].id,
        }
        actual_ids = {r["contract_id"] for r in rows}
        assert actual_ids == expected_ids
        assert 0 not in actual_ids


class TestPeriodInvoiceListSummary:
    """P-06一覧APIの全体合計（8%・10%をまたいだ合計）。

    フロントで税抜同士を足し算させない（CLAUDE.md冒頭のルール）ため、
    バックエンドがDecimalのまま合算して返す値をfixtures非依存で検証する。
    """

    def test_summary_sums_standard_and_reduced(self, db, branch_graph):
        from app.api.statements import list_invoices

        generator.generate(db, branch_graph["period"].id)
        result = list_invoices(branch_graph["period"].id, db)

        assert len(result.items) == 2
        # 10%: X(1000) + Y(2000+300) + Z(200) = 3500、消費税 CEIL(3500*0.1)=350
        # 8%: W(500)、消費税 CEIL(500*0.08)=40
        # 合計: 税抜 3500+500=4000、消費税 350+40=390
        assert result.summary.total_ex_tax == Decimal("4000.00")
        assert result.summary.tax_amount == Decimal("390")
        assert result.summary.total_amount == Decimal("4390.00")


class TestSkipStatement:
    """明細不要（contract.skip_statement）フラグが生成に反映されること。

    P-03で立てたフラグが生成ロジックに繋がっていなかった欠陥
    （明細不要にしても普通に請求されてしまう）を固定する回帰テスト。
    """

    def test_skip_statement_contract_gets_no_statement(self, db, branch_graph):
        branch_graph["contracts"]["X"].skip_statement = True
        db.flush()

        generator.generate(db, branch_graph["period"].id)

        stmts = db.execute(
            select(InvoiceStatement).where(
                InvoiceStatement.contract_id == branch_graph["contracts"]["X"].id
            )
        ).scalars().all()
        assert stmts == []

    def test_skip_statement_line_is_not_assigned(self, db, branch_graph):
        branch_graph["contracts"]["X"].skip_statement = True
        db.flush()

        generator.generate(db, branch_graph["period"].id)

        line = db.execute(
            select(BillingLine)
            .join(RentalOrder, RentalOrder.id == BillingLine.order_id)
            .where(RentalOrder.contract_id == branch_graph["contracts"]["X"].id)
        ).scalar_one()
        assert line.statement_id is None

    def test_other_contracts_unaffected(self, db, branch_graph):
        """契約Xだけ明細不要にしても、他契約の生成件数は変わらない。"""
        branch_graph["contracts"]["X"].skip_statement = True
        db.flush()

        summary = generator.generate(db, branch_graph["period"].id)
        # 全体(5明細書)から契約X分(1明細書)が抜ける
        assert summary.statements == 4
        assert summary.assigned_lines == 4

        stmts_z = db.execute(
            select(InvoiceStatement).where(
                InvoiceStatement.contract_id == branch_graph["contracts"]["Z"].id
            )
        ).scalars().all()
        assert len(stmts_z) == 1


class TestGuards:
    def test_locked_period_cannot_regenerate(self, db, branch_graph):
        branch_graph["period"].status = PeriodStatus.CONFIRMED
        db.flush()
        with pytest.raises(generator.PeriodLockedError):
            generator.generate(db, branch_graph["period"].id)

    def test_no_billable_lines_raises(self, db, branch_graph):
        db.execute(
            BillingLine.__table__.update()
            .where(BillingLine.period_id == branch_graph["period"].id)
            .values(is_billable=False)
        )
        db.flush()
        with pytest.raises(generator.NoBillableLinesError):
            generator.generate(db, branch_graph["period"].id)

    def test_unknown_period_raises(self, db):
        with pytest.raises(generator.PeriodNotFoundError):
            generator.generate(db, 999_999_999)

    def test_discount_lines_are_not_assigned(self, db, branch_graph):
        """値引行（is_billable=false）は明細書に割り当てない。"""
        order_x = db.execute(
            select(RentalOrder).where(RentalOrder.contract_id == branch_graph["contracts"]["X"].id)
        ).scalar_one()
        item_equip = db.execute(select(Item).where(Item.code == "GEN-EQUIP")).scalar_one()
        discount_line = BillingLine(
            period_id=branch_graph["period"].id, order_id=order_x.id, item_id=item_equip.id,
            item_name_snapshot="値引", quantity=Decimal("1"), unit_price=Decimal("-100"),
            unit_price_type=UnitPriceType.SALE, src_quantity=Decimal("1"), src_unit_price=Decimal("-100"),
            is_billable=False,
        )
        db.add(discount_line)
        db.flush()

        generator.generate(db, branch_graph["period"].id)
        db.refresh(discount_line)
        assert discount_line.statement_id is None
