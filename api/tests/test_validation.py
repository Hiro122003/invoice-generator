"""F-07 発行前チェックを検証する。

契約ごとに1本ずつ、目的の異なる明細を用意し、4種類のチェックが
それぞれ狙った契約だけを拾うことを確認する。

  VAL-A  今期・前期とも同額 → 何も出ない（陰性対照）
  VAL-B  generate() 実行後に追加した明細 → 請求漏れ
  VAL-C  今期のみ登場 → 新規契約
  VAL-D  前期のみ登場 → 消滅契約
  VAL-E  前期1,000円→今期50,000円 → 金額急増
  VAL-F  金額0円の明細（generate()前に追加） → 金額0円
  VAL-G  レンタル期間が請求期間の外（generate()前に追加） → 期間外
"""

import datetime as dt
from decimal import Decimal

import pytest

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
from app.services import generator, validation

PERIOD_START = dt.date(2095, 3, 1)
PERIOD_END = dt.date(2095, 3, 31)
PREV_PERIOD_START = dt.date(2095, 2, 1)
PREV_PERIOD_END = dt.date(2095, 2, 28)


def _line(period_id, order_id, item_id, qty, price, **kw):
    # price=None で金額0円の明細（基本料・単価とも未設定）を作れるようにする。
    unit_price = None if price is None else Decimal(price)
    return BillingLine(
        period_id=period_id,
        order_id=order_id,
        item_id=item_id,
        item_name_snapshot=kw.pop("item_name_snapshot", "テスト明細"),
        quantity=Decimal(qty),
        unit_price=unit_price,
        unit_price_type=UnitPriceType.SALE,
        src_quantity=Decimal(qty),
        src_unit_price=unit_price,
        **kw,
    )


@pytest.fixture
def val_graph(db):
    office = Office(code="VAL-OFC", name="テスト営業所")
    customer = Customer(code="VAL-CUST", name="テスト販売先")
    client = Client(name="VAL社", normalized_name="VAL社")
    rep = SalesRep(code="VAL-REP", name="テスト担当")
    db.add_all([office, customer, client, rep])
    db.flush()

    site = Site(name="VAL社 現場1", client_id=client.id)
    db.add(site)
    db.flush()

    def contract(no):
        return Contract(
            contract_no=no, customer_id=customer.id, site_id=site.id,
            sales_rep_id=rep.id, office_id=office.id,
        )

    contract_a, contract_c, contract_e, contract_f, contract_g = (
        contract(n) for n in ("VAL-A", "VAL-C", "VAL-E", "VAL-F", "VAL-G")
    )
    contract_d_prev = contract("VAL-D")

    item = Item(
        code="VAL-ITEM", name="テスト品目",
        tax_category=TaxCategory.STANDARD, billing_group=BillingGroup.EQUIPMENT,
    )

    period = BillingPeriod(start_date=PERIOD_START, end_date=PERIOD_END)
    prev_period = BillingPeriod(start_date=PREV_PERIOD_START, end_date=PREV_PERIOD_END)

    db.add_all(
        [contract_a, contract_c, contract_e, contract_f, contract_g, contract_d_prev,
         item, period, prev_period]
    )
    db.flush()

    order_a = RentalOrder(period_id=period.id, contract_id=contract_a.id, order_no="VAL-OA")
    order_a_prev = RentalOrder(period_id=prev_period.id, contract_id=contract_a.id, order_no="VAL-OA-P")
    order_c = RentalOrder(period_id=period.id, contract_id=contract_c.id, order_no="VAL-OC")
    order_d_prev = RentalOrder(period_id=prev_period.id, contract_id=contract_d_prev.id, order_no="VAL-OD-P")
    order_e = RentalOrder(period_id=period.id, contract_id=contract_e.id, order_no="VAL-OE")
    order_e_prev = RentalOrder(period_id=prev_period.id, contract_id=contract_e.id, order_no="VAL-OE-P")
    order_f = RentalOrder(period_id=period.id, contract_id=contract_f.id, order_no="VAL-OF")
    order_g = RentalOrder(period_id=period.id, contract_id=contract_g.id, order_no="VAL-OG")
    db.add_all([order_a, order_a_prev, order_c, order_d_prev, order_e, order_e_prev, order_f, order_g])
    db.flush()

    # generate() より前に投入する明細。ここに含まれるものは全部
    # generate() 実行時に明細書へ割り当てられる（=請求漏れにならない）。
    db.add_all(
        [
            _line(period.id, order_a.id, item.id, "1", "1000"),
            _line(prev_period.id, order_a_prev.id, item.id, "1", "1000"),
            _line(period.id, order_c.id, item.id, "1", "2000"),
            _line(prev_period.id, order_d_prev.id, item.id, "1", "3000"),
            _line(period.id, order_e.id, item.id, "1", "50000"),
            _line(prev_period.id, order_e_prev.id, item.id, "1", "1000"),
            # 金額0円（基本料・単価とも未設定）
            _line(
                period.id, order_f.id, item.id, "1", None,
                item_name_snapshot="ゼロ円明細",
            ),
            # レンタル期間が請求期間より前で完全に終わっている
            _line(
                period.id, order_g.id, item.id, "1", "1000",
                item_name_snapshot="期間外明細",
                rental_start=dt.date(2095, 1, 1), rental_end=dt.date(2095, 1, 31),
            ),
        ]
    )
    db.flush()

    generator.generate(db, period.id)
    generator.generate(db, prev_period.id)
    db.flush()

    # generate() より後に投入する。既存の明細書には含まれないため、
    # 「請求対象なのに明細書に入っていない」状態になる。
    contract_b = contract("VAL-B")
    db.add(contract_b)
    db.flush()
    order_b = RentalOrder(period_id=period.id, contract_id=contract_b.id, order_no="VAL-OB")
    db.add(order_b)
    db.flush()
    db.add(_line(period.id, order_b.id, item.id, "1", "5000"))
    db.flush()

    return {"period": period, "prev_period": prev_period}


def _by_category(issues, category):
    return [i for i in issues if i.category == category]


class TestValidatePeriod:
    def test_missing_statement_catches_only_post_generation_line(self, db, val_graph):
        result = validation.validate_period(db, val_graph["period"].id)
        missing = _by_category(result.issues, validation.CheckCategory.MISSING_STATEMENT)
        assert len(missing) == 1
        assert missing[0].contract_no == "VAL-B"
        assert missing[0].amount == Decimal("5000.00")
        assert missing[0].severity == validation.Severity.HIGH

    def test_zero_amount_catches_only_target_line(self, db, val_graph):
        result = validation.validate_period(db, val_graph["period"].id)
        zero = _by_category(result.issues, validation.CheckCategory.ZERO_AMOUNT)
        assert len(zero) == 1
        assert zero[0].contract_no == "VAL-F"
        assert zero[0].item_name == "ゼロ円明細"

    def test_out_of_period_catches_only_target_line(self, db, val_graph):
        result = validation.validate_period(db, val_graph["period"].id)
        out = _by_category(result.issues, validation.CheckCategory.OUT_OF_PERIOD)
        assert len(out) == 1
        assert out[0].contract_no == "VAL-G"
        assert out[0].item_name == "期間外明細"

    def test_new_contract_detected(self, db, val_graph):
        result = validation.validate_period(db, val_graph["period"].id)
        new = _by_category(result.issues, validation.CheckCategory.NEW_CONTRACT)
        # VAL-B/F/Gも前期には存在しないため、それぞれの本来のチェック
        # （請求漏れ／金額0円／期間外）と同時に新規契約としても正しく拾われる。
        assert {i.contract_no for i in new} == {"VAL-B", "VAL-C", "VAL-F", "VAL-G"}
        assert next(i for i in new if i.contract_no == "VAL-C").amount == Decimal("2000.00")

    def test_vanished_contract_detected(self, db, val_graph):
        result = validation.validate_period(db, val_graph["period"].id)
        vanished = _by_category(result.issues, validation.CheckCategory.VANISHED_CONTRACT)
        assert {i.contract_no for i in vanished} == {"VAL-D"}
        assert (
            next(i for i in vanished if i.contract_no == "VAL-D").previous_amount
            == Decimal("3000.00")
        )

    def test_amount_changed_detected_with_direction(self, db, val_graph):
        result = validation.validate_period(db, val_graph["period"].id)
        changed = _by_category(result.issues, validation.CheckCategory.AMOUNT_CHANGED)
        assert len(changed) == 1
        assert changed[0].contract_no == "VAL-E"
        assert changed[0].previous_amount == Decimal("1000.00")
        assert changed[0].amount == Decimal("50000.00")
        assert "増加" in changed[0].message

    def test_normal_contract_has_no_issues(self, db, val_graph):
        result = validation.validate_period(db, val_graph["period"].id)
        contract_nos = {i.contract_no for i in result.issues if i.contract_no}
        assert "VAL-A" not in contract_nos

    def test_small_month_over_month_change_is_not_reported(self, db, val_graph):
        """VAL-Aは前期・今期とも1,000円で変化なし。閾値以下のノイズも出さない。"""
        result = validation.validate_period(db, val_graph["period"].id)
        for category in (
            validation.CheckCategory.NEW_CONTRACT,
            validation.CheckCategory.VANISHED_CONTRACT,
            validation.CheckCategory.AMOUNT_CHANGED,
        ):
            assert all(i.contract_no != "VAL-A" for i in _by_category(result.issues, category))

    def test_previous_period_is_resolved(self, db, val_graph):
        result = validation.validate_period(db, val_graph["period"].id)
        assert result.previous_period_id == val_graph["prev_period"].id
        assert result.previous_period_label == "2095-02"

    def test_no_previous_period_skips_month_over_month_gracefully(self, db):
        # 開発DBには実データ（2025-03など）が既に入っているため、
        # val_graph の期間（2095年）を使うと必ず「前期」が見つかってしまう。
        # 実業務が始まるより明らかに前の日付を使い、本当に前期が存在しない
        # 状態を再現する。
        period = BillingPeriod(start_date=dt.date(1901, 1, 1), end_date=dt.date(1901, 1, 31))
        db.add(period)
        db.flush()

        result = validation.validate_period(db, period.id)
        assert result.previous_period_id is None
        assert result.previous_period_label is None
        for category in (
            validation.CheckCategory.NEW_CONTRACT,
            validation.CheckCategory.VANISHED_CONTRACT,
            validation.CheckCategory.AMOUNT_CHANGED,
        ):
            assert _by_category(result.issues, category) == []

    def test_unknown_period_raises(self, db, val_graph):
        with pytest.raises(validation.PeriodNotFoundError):
            validation.validate_period(db, 999_999_999)
