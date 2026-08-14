"""VBAの判定ロジックの移植が正しいことを検証する。

VBAでは同じ判定が複数モジュールにコピーされ、片方だけ直されて不整合が
起きていた。ここで固定して再発を防ぐ。
"""

from decimal import Decimal

import pytest

from app.domain import rules
from app.models.base import BillingGroup, TaxCategory, UnitPriceType


class TestTaxCategory:
    """VBA: If SourceData(r, 21) = "ＳＲ－ＷＰＥＴ" （完全一致）"""

    def test_water_bottle_is_reduced(self):
        assert rules.classify_tax_category("ＳＲ－ＷＰＥＴ") == TaxCategory.REDUCED

    def test_halfwidth_hyphen_does_not_match(self):
        """ハイフンが半角だと一致しない。VBAと同じ挙動。

        ＳＲ－ＷＰＥＴ のハイフンは全角 U+FF0D。ここを取り違えると
        8%の売上が丸ごと10%に混ざる。
        """
        assert rules.classify_tax_category("ＳＲ-ＷＰＥＴ") == TaxCategory.STANDARD

    def test_halfwidth_code_does_not_match(self):
        assert rules.classify_tax_category("SR-WPET") == TaxCategory.STANDARD

    def test_partial_match_does_not_count(self):
        """完全一致なので、前後に文字が付くと対象外。"""
        assert rules.classify_tax_category("ＳＲ－ＷＰＥＴ２") == TaxCategory.STANDARD

    @pytest.mark.parametrize("code", ["DM-001", "ＰＣＡ－Ｍ０１", "", None])
    def test_others_are_standard(self, code):
        assert rules.classify_tax_category(code) == TaxCategory.STANDARD


class TestBillingGroup:
    """VBA: If PrdCode Like "*ＰＣＡ*" （部分一致）"""

    @pytest.mark.parametrize(
        "code", ["ＰＣＡ", "ＰＣＡ－Ｍ０１", "ＸＸＰＣＡ９９", "ＰＣＡ－Ｃ０１"]
    )
    def test_contains_pca_is_counter(self, code):
        assert rules.classify_billing_group(code) == BillingGroup.COUNTER

    def test_halfwidth_pca_does_not_match(self):
        assert rules.classify_billing_group("PCA-M01") == BillingGroup.EQUIPMENT

    @pytest.mark.parametrize("code", ["DM-001", "ＳＲ－ＷＰＥＴ", "", None])
    def test_others_are_equipment(self, code):
        assert rules.classify_billing_group(code) == BillingGroup.EQUIPMENT


class TestBillable:
    """VBA: If Not PrdName Like "*値引*"（この条件のとき明細書に転記する）"""

    @pytest.mark.parametrize(
        "name", ["調整値引 モデル5 仕様024", "値引", "端数値引", "特別値引き"]
    )
    def test_discount_is_not_billable(self, name):
        assert rules.is_billable_item(name) is False

    @pytest.mark.parametrize("name", ["ルームエアコン 2.8kW", "ウォーターボトル 12L", ""])
    def test_normal_items_are_billable(self, name):
        assert rules.is_billable_item(name) is True


class TestClientNameNormalization:
    """VBA: ListTable.リスト表作成 / companyNameList.CreateCompanyList"""

    def test_splits_at_ideographic_space(self):
        assert (
            rules.normalize_client_name("サンプル工業株式会社　第02サンプル改修工事")
            == "サンプル工業株式会社"
        )

    def test_removes_ligature_corp(self):
        assert rules.normalize_client_name("㈱テスト建設　現場A") == "テスト建設"

    def test_removes_parenthesized_corp(self):
        """（株）は㈱に寄せてから除去される。"""
        assert rules.normalize_client_name("（株）テスト建設　現場A") == "テスト建設"

    def test_truncates_at_tentative_name(self):
        assert (
            rules.normalize_client_name("テスト建設（仮称）新築工事") == "テスト建設"
        )

    def test_keeps_leading_tentative_name(self):
        """「（仮称）」が先頭にある場合は残す。

        VBA が pos > 1 としていた箇所（pos > 0 ではない）。
        先頭で切ると会社名が空になってしまうため。
        """
        assert rules.normalize_client_name("（仮称）テスト建設") == "（仮称）テスト建設"

    def test_no_space_returns_whole(self):
        assert rules.normalize_client_name("テスト建設株式会社") == "テスト建設株式会社"

    def test_halfwidth_space_is_not_a_separator(self):
        """区切りは全角スペースのみ。半角では切らない（VBAと同じ）。"""
        assert rules.normalize_client_name("テスト建設 現場A") == "テスト建設 現場A"

    @pytest.mark.parametrize("value", ["", None])
    def test_empty(self, value):
        assert rules.normalize_client_name(value) == ""


class TestUnitPriceResolution:
    """VBA invoice10.bas の単価分岐。

        月単価あり            → MONTHLY, 月単価,       月換算
        月単価なし・AL列あり  → DAILY,   レンタル単価, 経過日数
        それ以外              → SALE,    販売単価,     なし
    """

    def test_monthly_wins(self):
        t, price, dur = rules.resolve_unit_price(
            monthly_rate=Decimal("180"),
            rental_rate=Decimal("180"),
            monthly_conversion=Decimal("1"),
            elapsed_days=Decimal("31"),
            sale_price=None,
        )
        assert (t, price, dur) == (UnitPriceType.MONTHLY, Decimal("180"), Decimal("1"))

    def test_daily_when_no_monthly(self):
        t, price, dur = rules.resolve_unit_price(
            monthly_rate=None,
            rental_rate=Decimal("33"),
            monthly_conversion=None,
            elapsed_days=Decimal("31"),
            sale_price=None,
        )
        assert (t, price, dur) == (UnitPriceType.DAILY, Decimal("33"), Decimal("31"))

    def test_sale_when_neither(self):
        t, price, dur = rules.resolve_unit_price(
            monthly_rate=None,
            rental_rate=None,
            monthly_conversion=None,
            elapsed_days=None,
            sale_price=Decimal("3000"),
        )
        assert (t, price, dur) == (UnitPriceType.SALE, Decimal("3000"), None)

    def test_sale_has_no_duration(self):
        """SALE は期間を持たない。生成列の COALESCE(duration,1) が係数1になる。"""
        _, _, dur = rules.resolve_unit_price(None, None, None, Decimal("31"), Decimal("100"))
        assert dur is None


class TestBaseCharge:
    """VBA: If BasicCharge = 0 Then dataArr(7) = ""（0は空扱い）"""

    def test_zero_becomes_none(self):
        assert rules.normalize_base_charge(Decimal("0")) is None

    def test_none_stays_none(self):
        assert rules.normalize_base_charge(None) is None

    def test_value_is_kept(self):
        assert rules.normalize_base_charge(Decimal("500")) == Decimal("500")


class TestDecimalConversion:
    """openpyxl が返す値を float を経由せず Decimal にする。"""

    def test_int(self):
        assert rules.to_decimal(3) == Decimal("3")

    def test_float_does_not_leak_binary_error(self):
        """Decimal(0.1) は 0.1000000000000000055511... になる。str を挟んで防ぐ。"""
        assert rules.to_decimal(0.1) == Decimal("0.1")

    def test_float_integral(self):
        assert rules.to_decimal(33.0) == Decimal("33.0")

    def test_string_with_comma(self):
        assert rules.to_decimal("1,400") == Decimal("1400")

    @pytest.mark.parametrize("value", [None, "", "  ", "abc", True])
    def test_non_numeric_becomes_none(self, value):
        assert rules.to_decimal(value) is None

    def test_negative(self):
        assert rules.to_decimal(-445) == Decimal("-445")
