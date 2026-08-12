"""明細行の金額（生成列）が現行VBAと一致することを検証する。

VBA は明細書のセルに次の数式を書き込んでいた。

    日数/月数あり :  =(G*F)+(H*I*F)
    日数/月数なし :  =(G*F)+(H*F)
        F=数量  G=基本料  H=単価  I=日数/月数  J=金額

これを COALESCE(duration, 1) で1本に統合したものが billing_line.amount。
期待値は fixtures/202603.xlsx の実データから取っている。
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.models import BillingLine, UnitPriceType


def make_line(db: Session, graph: dict, **kwargs) -> BillingLine:
    line = BillingLine(
        period_id=graph["period"].id,
        order_id=graph["order"].id,
        item_id=graph["item"].id,
        item_name_snapshot="テスト品目",
        **kwargs,
    )
    db.add(line)
    db.flush()
    db.refresh(line)  # 生成列はDBが計算するので読み直す
    return line


class TestVbaParity:
    """実データの行をそのまま再現して突き合わせる。"""

    def test_daily_rate(self, db, fixture_graph):
        """日単価 × 経過日数 × 数量。

        実データ1行目: 数量3 / 日単価33 / 経過日数31 → 小計 3,069
        VBA: =(G*F)+(H*I*F) = (0*3)+(33*31*3)
        """
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("3"),
            base_charge=Decimal("0"),
            unit_price=Decimal("33"),
            duration=Decimal("31"),
            unit_price_type=UnitPriceType.DAILY,
        )
        assert line.amount == Decimal("3069.00")

    def test_monthly_rate(self, db, fixture_graph):
        """月単価 × 月換算 × 数量。

        実データ: 数量2 / 月単価180 / 月換算1 → 小計 360
        """
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("2"),
            base_charge=None,
            unit_price=Decimal("180"),
            duration=Decimal("1"),
            unit_price_type=UnitPriceType.MONTHLY,
        )
        assert line.amount == Decimal("360.00")

    def test_sale_price_without_duration(self, db, fixture_graph):
        """販売単価は期間を持たない。duration が NULL のケース。

        実データ: 数量1 / 販売単価3,000 → 小計 3,000
        VBA: =(G*F)+(H*F) = (0*1)+(3000*1)
        COALESCE(duration, 1) が係数1として効くことの確認。
        """
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("1"),
            base_charge=None,
            unit_price=Decimal("3000"),
            duration=None,
            unit_price_type=UnitPriceType.SALE,
        )
        assert line.amount == Decimal("3000.00")

    def test_water_bottle_8pct(self, db, fixture_graph):
        """8%対象（ＳＲ－ＷＰＥＴ）。数量2 × 単価1,400 → 2,800"""
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("2"),
            unit_price=Decimal("1400"),
            duration=None,
            unit_price_type=UnitPriceType.SALE,
        )
        assert line.amount == Decimal("2800.00")

    def test_counter_charge(self, db, fixture_graph):
        """カウンタ料金。数量1,510 × 単価25 → 37,750"""
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("1510"),
            unit_price=Decimal("25"),
            duration=None,
            unit_price_type=UnitPriceType.SALE,
        )
        assert line.amount == Decimal("37750.00")

    def test_base_charge_is_added(self, db, fixture_graph):
        """基本料は数量倍されて加算される。(500*2)+(100*10*2) = 3,000"""
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("2"),
            base_charge=Decimal("500"),
            unit_price=Decimal("100"),
            duration=Decimal("10"),
            unit_price_type=UnitPriceType.DAILY,
        )
        assert line.amount == Decimal("3000.00")

    def test_negative_amount_is_allowed(self, db, fixture_graph):
        """値引行はマイナス金額。行として保持できる必要がある。"""
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("1"),
            unit_price=Decimal("-445"),
            duration=None,
            unit_price_type=UnitPriceType.SALE,
            is_billable=False,
        )
        assert line.amount == Decimal("-445.00")
        assert line.is_billable is False


class TestGeneratedColumnIsEnforced:
    """金額は計算結果でしかありえない、という不変条件の確認。"""

    def test_cannot_write_amount_directly(self, db, fixture_graph):
        """amount への直接書き込みはDBが拒否する。

        アプリにバグがあっても「数量×単価と金額が食い違う行」を作れない。
        """
        with pytest.raises(DatabaseError):
            make_line(
                db,
                fixture_graph,
                quantity=Decimal("1"),
                unit_price=Decimal("100"),
                amount=Decimal("999999"),
            )

    def test_amount_follows_edits(self, db, fixture_graph):
        """手修正すると金額が自動で追随する（F-05の中核）。"""
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("3"),
            unit_price=Decimal("33"),
            duration=Decimal("31"),
            src_quantity=Decimal("3"),
            src_unit_price=Decimal("33"),
            src_duration=Decimal("31"),
        )
        assert line.amount == Decimal("3069.00")
        assert line.is_edited is False

        # 利用者が数量を 3 → 5 に修正
        line.quantity = Decimal("5")
        db.flush()
        db.refresh(line)

        assert line.amount == Decimal("5115.00")  # 33 * 31 * 5
        assert line.is_edited is True


class TestDecimalIsPreserved:
    """浮動小数点を経由していないことの確認。"""

    def test_amount_is_decimal_not_float(self, db, fixture_graph):
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("1"),
            unit_price=Decimal("33"),
            duration=Decimal("31"),
        )
        assert isinstance(line.amount, Decimal)

    def test_no_rounding_error_on_repeated_thirds(self, db, fixture_graph):
        """0.1 の足し合わせで誤差が出ないこと。float なら 0.30000000000000004 になる。"""
        line = make_line(
            db,
            fixture_graph,
            quantity=Decimal("3"),
            unit_price=Decimal("0.10"),
            duration=None,
        )
        assert line.amount == Decimal("0.30")
