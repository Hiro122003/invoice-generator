"""fixtures/202603.xlsx を実際に取り込み、現行VBAと同じ数字が出るか検証する。

期待値の根拠は docs/vba-analysis.md 7章。
参照ファイルはリポジトリに含まれないため、無い環境では skip する。
"""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import (
    AppUser,
    BillingLine,
    Client,
    Contract,
    Item,
    RentalOrder,
    Site,
    TaxCategory,
    UnitPriceType,
)
from app.models.base import BillingGroup, UserRole
from app.services import excel_reader, importer

FIXTURE = Path(settings.fixtures_dir) / "202603.xlsx"

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason=f"参照データがありません: {FIXTURE}",
)

# 現行VBAの出力（docs/vba-analysis.md 7章）
EXPECTED = {
    "rows": 937,
    "contracts": 81,
    "clients": 5,
    "sites": 80,
    "total_standard": Decimal("2583740"),
    "total_reduced": Decimal("35000"),
    "discount_rows": 10,
    "discount_total": Decimal("-74977"),
    "reduced_rows": 10,
    "counter_rows": 10,
    "monthly": 52,
    "daily": 817,
    "sale": 68,
}


@pytest.fixture
def imported(db):
    """参照データを取り込む。テスト終了時にロールバックされる。"""
    user = AppUser(
        login_id="pytest", display_name="テスト", role=UserRole.APPROVER
    )
    db.add(user)
    db.flush()

    result = excel_reader.parse(str(FIXTURE))
    assert not result.has_error, [i.message for i in result.errors]
    summary = importer.run_import(db, result, FIXTURE.name, user)
    return summary


def _count(db, model, *where):
    return db.execute(
        select(func.count()).select_from(model).where(*where)
    ).scalar_one()


def _count_lines(db, period_id: int, *where):
    """明細行は必ず請求期間で絞る。

    絞らないと開発用に投入済みの他の月まで数えてしまう。
    本番の集計クエリでも同じ制約がかかるので、テストも同じ形にしておく。
    """
    return db.execute(
        select(func.count())
        .select_from(BillingLine)
        .where(BillingLine.period_id == period_id, BillingLine.deleted_at.is_(None))
        .where(*where)
    ).scalar_one()


def _sum_amount(db, period_id: int, *where):
    stmt = (
        select(func.sum(BillingLine.amount))
        .join(Item, Item.id == BillingLine.item_id)
        .where(BillingLine.period_id == period_id, BillingLine.deleted_at.is_(None))
    )
    for w in where:
        stmt = stmt.where(w)
    return db.execute(stmt).scalar_one()


class TestImportedVolume:
    def test_row_count(self, db, imported):
        assert imported.inserted_lines == EXPECTED["rows"]
        assert _count_lines(db, imported.period_id) == EXPECTED["rows"]

    def test_contracts(self, db, imported):
        """契約はこの期間に登場したものを数える（マスタ全体ではない）。"""
        actual = db.execute(
            select(func.count(func.distinct(RentalOrder.contract_id))).where(
                RentalOrder.period_id == imported.period_id
            )
        ).scalar_one()
        assert actual == EXPECTED["contracts"]

    def test_clients_are_name_normalized(self, db, imported):
        """納品先80件が、名寄せ後は5社にまとまる。"""
        sites = db.execute(
            select(func.count(func.distinct(Contract.site_id)))
            .select_from(RentalOrder)
            .join(Contract, Contract.id == RentalOrder.contract_id)
            .where(RentalOrder.period_id == imported.period_id)
        ).scalar_one()
        clients = db.execute(
            select(func.count(func.distinct(Site.client_id)))
            .select_from(RentalOrder)
            .join(Contract, Contract.id == RentalOrder.contract_id)
            .join(Site, Site.id == Contract.site_id)
            .where(RentalOrder.period_id == imported.period_id)
        ).scalar_one()
        assert sites == EXPECTED["sites"]
        assert clients == EXPECTED["clients"]

    def test_orders_link_to_lines(self, db, imported):
        """全明細が受注にぶら下がっている（孤児がない）。"""
        orphans = db.execute(
            select(func.count())
            .select_from(BillingLine)
            .outerjoin(RentalOrder, RentalOrder.id == BillingLine.order_id)
            .where(BillingLine.period_id == imported.period_id)
            .where(RentalOrder.id.is_(None))
        ).scalar_one()
        assert orphans == 0


class TestAmountsMatchVba:
    """金額が1円でもずれたら移植失敗。"""

    def test_standard_rate_total(self, db, imported):
        total = _sum_amount(
            db,
            imported.period_id,
            BillingLine.is_billable,
            Item.tax_category == TaxCategory.STANDARD,
        )
        assert total == EXPECTED["total_standard"]

    def test_reduced_rate_total(self, db, imported):
        total = _sum_amount(
            db,
            imported.period_id,
            BillingLine.is_billable,
            Item.tax_category == TaxCategory.REDUCED,
        )
        assert total == EXPECTED["total_reduced"]

    def test_amount_matches_source_subtotal(self, db, imported):
        """DBが計算した金額が、元データの「小計」列と全行一致する。

        生成列の式が VBA の =(G*F)+(H*I*F) と等価であることの、
        937行ぶんの突き合わせ。
        """
        result = excel_reader.parse(str(FIXTURE))
        source_total = sum(
            r.source_amount for r in result.rows if r.source_amount is not None
        )
        db_total = db.execute(
            select(func.sum(BillingLine.amount)).where(
                BillingLine.period_id == imported.period_id
            )
        ).scalar_one()
        assert db_total == source_total


class TestClassification:
    def test_discount_rows_excluded(self, db, imported):
        """値引行は請求対象外として保持される（削除しない）。"""
        rows = _count_lines(db, imported.period_id, ~BillingLine.is_billable)
        assert rows == EXPECTED["discount_rows"]

        total = db.execute(
            select(func.sum(BillingLine.amount)).where(
                BillingLine.period_id == imported.period_id,
                ~BillingLine.is_billable,
            )
        ).scalar_one()
        assert total == EXPECTED["discount_total"]

    def test_reduced_tax_rows(self, db, imported):
        """ＳＲ－ＷＰＥＴ の10行が8%に分類される。全角判定が効いている証拠。"""
        rows = db.execute(
            select(func.count())
            .select_from(BillingLine)
            .join(Item, Item.id == BillingLine.item_id)
            .where(
                BillingLine.period_id == imported.period_id,
                Item.tax_category == TaxCategory.REDUCED,
            )
        ).scalar_one()
        assert rows == EXPECTED["reduced_rows"]

    def test_counter_rows(self, db, imported):
        """ＰＣＡ を含む10行がカウンタに分類される。"""
        rows = db.execute(
            select(func.count())
            .select_from(BillingLine)
            .join(Item, Item.id == BillingLine.item_id)
            .where(
                BillingLine.period_id == imported.period_id,
                Item.billing_group == BillingGroup.COUNTER,
            )
        ).scalar_one()
        assert rows == EXPECTED["counter_rows"]

    def test_counter_is_still_standard_rate(self, db, imported):
        """カウンタは明細書を分けるだけで、税率は10%のまま。

        ここを取り違えると10%の合計が163,500円ずれる。
        """
        rows = db.execute(
            select(func.count())
            .select_from(Item)
            .where(
                Item.billing_group == BillingGroup.COUNTER,
                Item.tax_category != TaxCategory.STANDARD,
            )
        ).scalar_one()
        assert rows == 0

    def test_unit_price_types(self, db, imported):
        for kind, expected in (
            (UnitPriceType.MONTHLY, EXPECTED["monthly"]),
            (UnitPriceType.DAILY, EXPECTED["daily"]),
            (UnitPriceType.SALE, EXPECTED["sale"]),
        ):
            actual = _count_lines(
                db, imported.period_id, BillingLine.unit_price_type == kind
            )
            assert actual == expected, f"{kind}: {actual} != {expected}"

    def test_sale_rows_have_no_duration(self, db, imported):
        rows = _count_lines(
            db,
            imported.period_id,
            BillingLine.unit_price_type == UnitPriceType.SALE,
            BillingLine.duration.isnot(None),
        )
        assert rows == 0


class TestSourceValuesRecorded:
    """取込時の値が src_* に控えられている（手修正の差分判定に使う）。"""

    def test_src_equals_current_after_import(self, db, imported):
        """取込直後は編集可能4項目が src_* と完全一致する。

        NULL 同士も一致とみなすため IS DISTINCT FROM で比較する。
        """
        mismatched = _count_lines(
            db,
            imported.period_id,
            BillingLine.quantity.is_distinct_from(BillingLine.src_quantity)
            | BillingLine.base_charge.is_distinct_from(BillingLine.src_base_charge)
            | BillingLine.unit_price.is_distinct_from(BillingLine.src_unit_price)
            | BillingLine.duration.is_distinct_from(BillingLine.src_duration),
        )
        assert mismatched == 0

    def test_no_line_is_edited_right_after_import(self, db, imported):
        line = db.execute(
            select(BillingLine)
            .where(BillingLine.period_id == imported.period_id)
            .limit(1)
        ).scalar_one()
        assert line.is_edited is False
