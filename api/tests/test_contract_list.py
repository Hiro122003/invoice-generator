"""F-03 リスト表のフィルタ・集計・明細不要トグルを検証する。

api/app/api/contracts.py の SQL 組み立てとビジネスロジックを、
実データに依存しない自作データセットで検証する。
"""

import datetime as dt
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.contracts import Filters, _fetch_rows
from app.models import (
    BillingLine,
    BillingPeriod,
    Client,
    Contract,
    Customer,
    Item,
    Office,
    RentalOrder,
    SalesRep,
    Site,
)
from app.models.base import BillingGroup, TaxCategory, UnitPriceType

PERIOD_START = dt.date(2098, 1, 1)
PERIOD_END = dt.date(2098, 1, 31)


@pytest.fixture
def multi_contract_graph(db: Session) -> dict:
    """税区分・請求グループが異なる3契約を用意する。

    契約A: 10%の備品のみ
    契約B: 8%の品目を含む（明細不要=true）
    契約C: カウンタ類を含む
    """
    office = Office(code="F03-OFC", name="テスト営業所")
    customer = Customer(code="F03-CUST", name="テスト販売先")
    client_a = Client(name="A社", normalized_name="A社")
    client_b = Client(name="B社", normalized_name="B社")
    rep = SalesRep(code="F03-REP", name="テスト担当")
    db.add_all([office, customer, client_a, client_b, rep])
    db.flush()

    site_a = Site(name="A社 現場1", address="住所A", client_id=client_a.id)
    site_b = Site(name="B社 現場2", address="住所B", client_id=client_b.id)
    site_c = Site(name="A社 現場3", address="住所C", client_id=client_a.id)
    db.add_all([site_a, site_b, site_c])
    db.flush()

    contract_a = Contract(
        contract_no="F03-A", customer_id=customer.id, site_id=site_a.id,
        sales_rep_id=rep.id, office_id=office.id,
    )
    contract_b = Contract(
        contract_no="F03-B", customer_id=customer.id, site_id=site_b.id,
        sales_rep_id=rep.id, office_id=office.id, skip_statement=True,
    )
    contract_c = Contract(
        contract_no="F03-C", customer_id=customer.id, site_id=site_c.id,
        sales_rep_id=rep.id, office_id=office.id,
    )
    item_standard = Item(
        code="F03-ITEM-STD", name="標準品", tax_category=TaxCategory.STANDARD,
        billing_group=BillingGroup.EQUIPMENT,
    )
    item_reduced = Item(
        code="F03-ITEM-RED", name="軽減税率品", tax_category=TaxCategory.REDUCED,
        billing_group=BillingGroup.EQUIPMENT,
    )
    item_counter = Item(
        code="F03-ITEM-CNT", name="カウンタ品", tax_category=TaxCategory.STANDARD,
        billing_group=BillingGroup.COUNTER,
    )
    period = BillingPeriod(start_date=PERIOD_START, end_date=PERIOD_END)
    db.add_all([contract_a, contract_b, contract_c, item_standard, item_reduced, item_counter, period])
    db.flush()

    order_a = RentalOrder(period_id=period.id, contract_id=contract_a.id, order_no="F03-OA")
    order_b = RentalOrder(period_id=period.id, contract_id=contract_b.id, order_no="F03-OB")
    order_c = RentalOrder(period_id=period.id, contract_id=contract_c.id, order_no="F03-OC")
    db.add_all([order_a, order_b, order_c])
    db.flush()

    def line(order, item, quantity, unit_price, billable=True):
        return BillingLine(
            period_id=period.id, order_id=order.id, item_id=item.id,
            item_name_snapshot=item.name, quantity=Decimal(quantity),
            unit_price=Decimal(unit_price), duration=None,
            unit_price_type=UnitPriceType.SALE,
            src_quantity=Decimal(quantity), src_unit_price=Decimal(unit_price),
            is_billable=billable,
        )

    db.add_all([
        line(order_a, item_standard, 1, 1000),
        line(order_b, item_standard, 1, 500),
        line(order_b, item_reduced, 2, 300),  # 契約Bは10%と8%両方持つ
        line(order_c, item_counter, 10, 25),
        line(order_c, item_standard, 1, 100, billable=False),  # 値引行。集計から除外される
    ])
    db.flush()

    return {"period": period, "contract_a": contract_a, "contract_b": contract_b, "contract_c": contract_c}


class TestFilterValidation:
    def test_invalid_tax_rejected(self):
        with pytest.raises(HTTPException) as exc:
            Filters(None, None, None, "INVALID", None, None, None, None)
        assert exc.value.status_code == 422

    def test_invalid_group_rejected(self):
        with pytest.raises(HTTPException) as exc:
            Filters(None, None, None, None, "INVALID", None, None, None)
        assert exc.value.status_code == 422


class TestContractListing:
    def test_no_filter_returns_all_contracts_in_period(self, db, multi_contract_graph):
        f = Filters(None, None, None, None, None, None, None, None)
        rows = _fetch_rows(db, multi_contract_graph["period"].id, f)
        assert {r["contract_no"] for r in rows} == {"F03-A", "F03-B", "F03-C"}

    def test_amount_excludes_non_billable_lines(self, db, multi_contract_graph):
        """値引行（is_billable=false）は税抜金額の集計に含めない。"""
        f = Filters(None, None, None, None, None, None, None, None)
        rows = {r["contract_no"]: r for r in _fetch_rows(db, multi_contract_graph["period"].id, f)}
        # 契約C: カウンタ 10*25=250 のみ課金対象。値引100は除外
        assert rows["F03-C"]["total_ex_tax"] == Decimal("250.00")

    def test_line_count_includes_non_billable(self, db, multi_contract_graph):
        """明細行数は値引行も含めて数える（税抜金額とは別集計）。"""
        f = Filters(None, None, None, None, None, None, None, None)
        rows = {r["contract_no"]: r for r in _fetch_rows(db, multi_contract_graph["period"].id, f)}
        assert rows["F03-C"]["line_count"] == 2

    def test_client_partial_match(self, db, multi_contract_graph):
        f = Filters("A社", None, None, None, None, None, None, None)
        rows = _fetch_rows(db, multi_contract_graph["period"].id, f)
        assert {r["contract_no"] for r in rows} == {"F03-A", "F03-C"}

    def test_contract_no_partial_match(self, db, multi_contract_graph):
        f = Filters(None, None, "F03-B", None, None, None, None, None)
        rows = _fetch_rows(db, multi_contract_graph["period"].id, f)
        assert {r["contract_no"] for r in rows} == {"F03-B"}

    def test_tax_reduced_filter(self, db, multi_contract_graph):
        """8%を含む契約だけが返る。10%しかない契約は出ない。"""
        f = Filters(None, None, None, TaxCategory.REDUCED, None, None, None, None)
        rows = _fetch_rows(db, multi_contract_graph["period"].id, f)
        assert {r["contract_no"] for r in rows} == {"F03-B"}

    def test_group_counter_filter(self, db, multi_contract_graph):
        f = Filters(None, None, None, None, BillingGroup.COUNTER, None, None, None)
        rows = _fetch_rows(db, multi_contract_graph["period"].id, f)
        assert {r["contract_no"] for r in rows} == {"F03-C"}

    def test_skip_statement_filter(self, db, multi_contract_graph):
        f = Filters(None, None, None, None, None, True, None, None)
        rows = _fetch_rows(db, multi_contract_graph["period"].id, f)
        assert {r["contract_no"] for r in rows} == {"F03-B"}

    def test_amount_range_filter(self, db, multi_contract_graph):
        # 契約A=1000, 契約B=500+600=1100, 契約C=250
        f = Filters(None, None, None, None, None, None, Decimal("500"), Decimal("1050"))
        rows = _fetch_rows(db, multi_contract_graph["period"].id, f)
        assert {r["contract_no"] for r in rows} == {"F03-A"}

    def test_other_period_not_included(self, db, multi_contract_graph):
        """別の請求期間しか登場しない契約は出てこない（period_id で厳密に絞る）。"""
        other_period = BillingPeriod(start_date=dt.date(2098, 2, 1), end_date=dt.date(2098, 2, 28))
        db.add(other_period)
        db.flush()
        f = Filters(None, None, None, None, None, None, None, None)
        rows = _fetch_rows(db, other_period.id, f)
        assert rows == []


class TestSkipStatementToggle:
    def test_toggle_persists(self, db, multi_contract_graph):
        contract = multi_contract_graph["contract_a"]
        assert contract.skip_statement is False

        contract.skip_statement = True
        db.flush()
        db.expire(contract)

        reloaded = db.get(Contract, contract.id)
        assert reloaded.skip_statement is True
