"""F-08 確定・締めを検証する。

確定時に invoice / invoice_statement へ金額をスナップショットし、
確定解除でクリアして「常に集計クエリで算出」の状態へ戻す。
版数（invoice.revision）は「確定するたびに+1、第1版は1」
（docs/design.md）。
"""

import datetime as dt
from decimal import Decimal

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
    InvoiceStatement,
    Item,
    Office,
    PeriodUnlockLog,
    RentalOrder,
    SalesRep,
    Site,
)
from app.models.base import BillingGroup, PeriodStatus, TaxCategory, UnitPriceType, UserRole
from app.services import confirmation, generator

PERIOD_START = dt.date(2094, 1, 1)
PERIOD_END = dt.date(2094, 1, 31)


@pytest.fixture
def confirm_graph(db):
    """生成済みの請求書を1件持つ最小データセット。"""
    office = Office(code="CFM-OFC", name="テスト営業所")
    customer = Customer(code="CFM-CUST", name="テスト販売先")
    client = Client(name="CFM社", normalized_name="CFM社")
    rep = SalesRep(code="CFM-REP", name="テスト担当")
    db.add_all([office, customer, client, rep])
    db.flush()

    site = Site(name="CFM社 現場1", client_id=client.id)
    db.add(site)
    db.flush()

    contract = Contract(
        contract_no="CFM-A", customer_id=customer.id, site_id=site.id,
        sales_rep_id=rep.id, office_id=office.id,
    )
    item = Item(
        code="CFM-ITEM", name="テスト品目",
        tax_category=TaxCategory.STANDARD, billing_group=BillingGroup.EQUIPMENT,
    )
    period = BillingPeriod(start_date=PERIOD_START, end_date=PERIOD_END)
    db.add_all([contract, item, period])
    db.flush()

    order = RentalOrder(period_id=period.id, contract_id=contract.id, order_no="CFM-O1")
    db.add(order)
    db.flush()

    line = BillingLine(
        period_id=period.id, order_id=order.id, item_id=item.id,
        item_name_snapshot=item.name, quantity=Decimal("2"), unit_price=Decimal("1000"),
        unit_price_type=UnitPriceType.SALE,
        src_quantity=Decimal("2"), src_unit_price=Decimal("1000"),
    )
    db.add(line)
    db.flush()

    user = AppUser(login_id="cfm-test", display_name="確定太郎", role=UserRole.APPROVER)
    db.add(user)
    db.flush()

    generator.generate(db, period.id)
    db.flush()

    return {"period": period, "user": user, "contract": contract}


class TestConfirm:
    def test_confirm_locks_period(self, db, confirm_graph):
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()
        db.refresh(period)
        assert period.status == PeriodStatus.CONFIRMED
        assert period.confirmed_at is not None
        assert period.confirmed_by == confirm_graph["user"].id

    def test_confirm_snapshots_invoice_totals(self, db, confirm_graph):
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()

        invoice = db.execute(
            select(Invoice).where(Invoice.period_id == period.id)
        ).scalar_one()
        # 税抜2000、消費税 CEIL(2000*0.1)=200
        assert invoice.total_ex_tax == Decimal("2000.00")
        assert invoice.tax_amount == Decimal("200")
        assert invoice.total_amount == Decimal("2200.00")
        assert invoice.confirmed_at is not None
        assert invoice.confirmed_by == confirm_graph["user"].id
        assert invoice.revision == 1

    def test_confirm_snapshots_statement_totals(self, db, confirm_graph):
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()

        invoice = db.execute(
            select(Invoice).where(Invoice.period_id == period.id)
        ).scalar_one()
        statement = db.execute(
            select(InvoiceStatement).where(InvoiceStatement.invoice_id == invoice.id)
        ).scalar_one()
        assert statement.total_ex_tax == Decimal("2000.00")
        assert statement.tax_amount == Decimal("200")
        assert statement.total_amount == Decimal("2200.00")

    def test_confirm_without_invoices_raises(self, db, confirm_graph):
        period2 = BillingPeriod(start_date=dt.date(2094, 2, 1), end_date=dt.date(2094, 2, 28))
        db.add(period2)
        db.flush()
        with pytest.raises(confirmation.NothingToConfirmError):
            confirmation.confirm_period(db, period2.id, confirm_graph["user"])

    def test_confirm_already_confirmed_raises(self, db, confirm_graph):
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()
        with pytest.raises(confirmation.AlreadyConfirmedError):
            confirmation.confirm_period(db, period.id, confirm_graph["user"])

    def test_confirm_unknown_period_raises(self, db, confirm_graph):
        with pytest.raises(confirmation.PeriodNotFoundError):
            confirmation.confirm_period(db, 999_999_999, confirm_graph["user"])

    def test_generate_refuses_after_confirm(self, db, confirm_graph):
        """確定済み期間は再生成できない（generator.generate() 側の既存ガード）。"""
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()
        with pytest.raises(generator.PeriodLockedError):
            generator.generate(db, period.id)


class TestUnconfirm:
    def test_unconfirm_unlocks_period(self, db, confirm_graph):
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()

        confirmation.unconfirm_period(db, period.id, confirm_graph["user"], "先方からの指摘")
        db.flush()
        db.refresh(period)
        assert period.status == PeriodStatus.DRAFT

    def test_unconfirm_clears_snapshots(self, db, confirm_graph):
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()

        confirmation.unconfirm_period(db, period.id, confirm_graph["user"], "訂正のため")
        db.flush()

        invoice = db.execute(
            select(Invoice).where(Invoice.period_id == period.id)
        ).scalar_one()
        assert invoice.total_ex_tax is None
        assert invoice.tax_amount is None
        assert invoice.total_amount is None
        # revision と confirmed_at は次回確定の判定・履歴のために残す
        assert invoice.revision == 1
        assert invoice.confirmed_at is not None

        statement = db.execute(
            select(InvoiceStatement).where(InvoiceStatement.invoice_id == invoice.id)
        ).scalar_one()
        assert statement.total_ex_tax is None

    def test_unconfirm_logs_reason(self, db, confirm_graph):
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()

        confirmation.unconfirm_period(db, period.id, confirm_graph["user"], "金額誤りのため")
        db.flush()

        log = db.execute(
            select(PeriodUnlockLog).where(PeriodUnlockLog.period_id == period.id)
        ).scalar_one()
        assert log.reason == "金額誤りのため"
        assert log.from_revision == 1
        assert log.unlocked_by == confirm_graph["user"].id

    def test_unconfirm_requires_reason(self, db, confirm_graph):
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()
        with pytest.raises(ValueError):
            confirmation.unconfirm_period(db, period.id, confirm_graph["user"], "   ")

    def test_unconfirm_not_confirmed_raises(self, db, confirm_graph):
        period = confirm_graph["period"]
        with pytest.raises(confirmation.NotConfirmedError):
            confirmation.unconfirm_period(db, period.id, confirm_graph["user"], "理由")


class TestRevisionBump:
    def test_reconfirm_after_unlock_bumps_revision(self, db, confirm_graph):
        period = confirm_graph["period"]

        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()
        confirmation.unconfirm_period(db, period.id, confirm_graph["user"], "訂正のため")
        db.flush()

        summary2 = confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()
        assert summary2.revision == 2

        invoice = db.execute(
            select(Invoice).where(Invoice.period_id == period.id)
        ).scalar_one()
        assert invoice.revision == 2

    def test_second_unlock_records_from_revision_2(self, db, confirm_graph):
        period = confirm_graph["period"]
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()
        confirmation.unconfirm_period(db, period.id, confirm_graph["user"], "訂正1")
        db.flush()
        confirmation.confirm_period(db, period.id, confirm_graph["user"])
        db.flush()
        confirmation.unconfirm_period(db, period.id, confirm_graph["user"], "訂正2")
        db.flush()

        logs = db.execute(
            select(PeriodUnlockLog)
            .where(PeriodUnlockLog.period_id == period.id)
            .order_by(PeriodUnlockLog.id)
        ).scalars().all()
        assert [log.from_revision for log in logs] == [1, 2]
