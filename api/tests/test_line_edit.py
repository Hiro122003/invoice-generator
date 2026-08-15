"""F-05 明細手修正 / F-10 修正履歴 を検証する。

金額を直接編集させず、4項目（数量・基本料・単価・日数/月数）からの
自動計算のままにする。1回のPATCHで明細行・明細書・請求書の3階層の
金額が連動することが本テストの核心。
"""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.lines import _apply_changes, _build_result
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
    LineEditLog,
    Office,
    RentalOrder,
    SalesRep,
    Site,
)
from app.models.base import BillingGroup, PeriodStatus, TaxCategory, UnitPriceType, UserRole
from app.services import generator

PERIOD_START = dt.date(2095, 1, 1)
PERIOD_END = dt.date(2095, 1, 31)


@pytest.fixture
def edit_graph(db):
    """生成済みの明細書を持つ最小データセット。"""
    office = Office(code="EDIT-OFC", name="テスト営業所")
    customer = Customer(code="EDIT-CUST", name="テスト販売先")
    client = Client(name="E社", normalized_name="E社")
    rep = SalesRep(code="EDIT-REP", name="テスト担当")
    db.add_all([office, customer, client, rep])
    db.flush()

    site = Site(name="E社 現場1", client_id=client.id)
    db.add(site)
    db.flush()

    contract = Contract(
        contract_no="EDIT-A", customer_id=customer.id, site_id=site.id,
        sales_rep_id=rep.id, office_id=office.id,
    )
    item = Item(code="EDIT-ITEM", name="テスト品目", tax_category=TaxCategory.STANDARD, billing_group=BillingGroup.EQUIPMENT)
    period = BillingPeriod(start_date=PERIOD_START, end_date=PERIOD_END)
    db.add_all([contract, item, period])
    db.flush()

    order = RentalOrder(period_id=period.id, contract_id=contract.id, order_no="EDIT-O1")
    db.add(order)
    db.flush()

    line = BillingLine(
        period_id=period.id, order_id=order.id, item_id=item.id,
        item_name_snapshot=item.name, quantity=Decimal("3"), unit_price=Decimal("33"),
        duration=Decimal("31"), unit_price_type=UnitPriceType.DAILY,
        src_quantity=Decimal("3"), src_unit_price=Decimal("33"), src_duration=Decimal("31"),
    )
    db.add(line)
    db.flush()

    user = AppUser(login_id="edit-test", display_name="テスト太郎", role=UserRole.APPROVER)
    db.add(user)
    db.flush()

    generator.generate(db, period.id)
    db.refresh(line)

    return {"period": period, "line": line, "user": user, "contract": contract}


class TestAmountFollowsEdit:
    """CLAUDE.md冒頭のルール: 金額は生成列。手で書けず、4項目から追随する。"""

    def test_quantity_change_updates_amount(self, db, edit_graph):
        line = edit_graph["line"]
        _apply_changes(db, line, {"quantity": Decimal("5")}, edit_graph["user"], None)
        db.flush()
        db.refresh(line)
        assert line.amount == Decimal("5115.00")  # 33*31*5

    def test_is_edited_flips_after_change(self, db, edit_graph):
        line = edit_graph["line"]
        assert line.is_edited is False
        _apply_changes(db, line, {"quantity": Decimal("5")}, edit_graph["user"], None)
        db.flush()
        db.refresh(line)
        assert line.is_edited is True

    def test_no_change_when_value_is_same(self, db, edit_graph):
        """同じ値へのPATCHは履歴を残さない（ログの水増し防止）。"""
        line = edit_graph["line"]
        _apply_changes(db, line, {"quantity": Decimal("3")}, edit_graph["user"], None)
        db.flush()
        logs = db.execute(
            select(LineEditLog).where(LineEditLog.line_id == line.id)
        ).scalars().all()
        assert logs == []


class TestThreeTierCascade:
    """1回の変更で 明細行 → 明細書 → 請求書 の3階層が連動する。"""

    def test_cascade_amounts(self, db, edit_graph):
        line = edit_graph["line"]
        _apply_changes(db, line, {"quantity": Decimal("5")}, edit_graph["user"], None)
        db.flush()
        db.refresh(line)

        result = _build_result(db, line)

        assert result.line.amount == Decimal("5115.00")
        assert result.statement is not None
        assert result.statement.total_ex_tax == Decimal("5115.00")
        assert result.statement.tax_amount == Decimal("512")  # CEIL(511.5)
        assert result.invoice is not None
        assert result.invoice.total_ex_tax == Decimal("5115.00")

    def test_ungenerated_line_has_no_statement(self, db, edit_graph):
        """明細書がまだ生成されていない明細行は statement/invoice が None。"""
        line = edit_graph["line"]
        line.statement_id = None
        db.flush()
        result = _build_result(db, line)
        assert result.statement is None
        assert result.invoice is None


class TestEditLog:
    """F-10 修正履歴。誰が・いつ・何を・いくらからいくらに変えたか。"""

    def test_log_records_old_and_new_value(self, db, edit_graph):
        line = edit_graph["line"]
        _apply_changes(db, line, {"quantity": Decimal("5")}, edit_graph["user"], "テスト理由")
        db.flush()

        log = db.execute(
            select(LineEditLog).where(LineEditLog.line_id == line.id)
        ).scalar_one()
        assert log.field == "quantity"
        assert log.old_value == Decimal("3.00")
        assert log.new_value == Decimal("5.00")
        assert log.edited_by == edit_graph["user"].id
        assert log.reason == "テスト理由"

    def test_multiple_field_changes_create_multiple_logs(self, db, edit_graph):
        line = edit_graph["line"]
        _apply_changes(
            db, line,
            {"quantity": Decimal("5"), "unit_price": Decimal("40")},
            edit_graph["user"], None,
        )
        db.flush()
        logs = db.execute(
            select(LineEditLog).where(LineEditLog.line_id == line.id)
        ).scalars().all()
        assert {l.field for l in logs} == {"quantity", "unit_price"}


class TestReset:
    """取込時の値（src_*）に戻す。"""

    def test_reset_restores_source_values(self, db, edit_graph):
        line = edit_graph["line"]
        _apply_changes(db, line, {"quantity": Decimal("5")}, edit_graph["user"], None)
        db.flush()
        db.refresh(line)
        assert line.amount == Decimal("5115.00")

        src_values = {
            "quantity": line.src_quantity,
            "base_charge": line.src_base_charge,
            "unit_price": line.src_unit_price,
            "duration": line.src_duration,
        }
        _apply_changes(db, line, src_values, edit_graph["user"], "取込時の値に戻す")
        db.flush()
        db.refresh(line)

        assert line.amount == Decimal("3069.00")
        assert line.is_edited is False


class TestLockedPeriod:
    """確定済み期間は編集不可。"""

    def test_confirmed_period_line_state(self, db, edit_graph):
        edit_graph["period"].status = PeriodStatus.CONFIRMED
        db.flush()

        from app.api.lines import _check_not_locked

        with pytest.raises(Exception):  # HTTPException
            _check_not_locked(db, edit_graph["line"])
