"""基幹システム側で削除された明細（Excel49列目「削除」）の扱い。

money-audit が指摘した欠陥の再発防止テスト。

修正前は「削除」列を一切読んでおらず、基幹側で取消・削除された行が
通常の請求対象行として投入されていた。fixtures/202603.xlsx はこの列が
全行空のため、受け入れテストでは検出できなかった経緯がある。
"""

import datetime as dt
import io
from decimal import Decimal

import openpyxl

from app.models import AppUser, BillingLine
from app.models.base import UserRole
from app.services import excel_reader, importer
from app.services.excel_reader import EXPECTED_COLUMNS

# 実運用ではありえない架空月にして、開発用に投入済みのデータと衝突させない
TEST_PERIOD_START = dt.date(2099, 1, 1)
TEST_PERIOD_END = dt.date(2099, 1, 31)


def _build_workbook(rows: list[dict], period_start=TEST_PERIOD_START, period_end=TEST_PERIOD_END) -> bytes:
    """テスト用の最小限のブックを作る。EXPECTED_COLUMNS の列構成に合わせる。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "匿名化データ"
    ws.append(list(EXPECTED_COLUMNS))

    base = {
        "識別番号": "T0000000001",
        "受注営業所コード": "OFC1",
        "受注営業所名": "テスト営業所",
        "販売先コード": "CUST1",
        "販売先名称": "テスト販売先株式会社",
        "販売先担当者": None,
        "営業担当者コード": "REP1",
        "営業担当者名": "テスト担当",
        "住所": "東京都テスト区",
        "受注番号": "ORDER1",
        "契約番号": "CONTRACT1",
        "初回納品日": None,
        "引取日": None,
        "取引先発注番号1": None,
        "取引先発注番号2": None,
        "取引先発注番号3": None,
        "取引先発注番号4": None,
        "取引先発注番号5": None,
        "納品先名称": "テスト現場",
        "納品日": None,
        "品番": "ITEM1",
        "品名": "テスト品目",
        "明細数量": 1,
        "商品摘要": None,
        "レンタル開始日": None,
        "レンタル終了日": None,
        "レンタル期間": None,
        "請求期間開始日": period_start,
        "請求期間終了日": period_end,
        "経過日数": None,
        "月換算": None,
        "単位": "日",
        "備考": None,
        "基本料": None,
        "月単価": None,
        "日単価": None,
        "販売単価": 1000,
        "レンタル単価": None,
        "レンタル金額": None,
        "販売金額": 1000,
        "小計": 1000,
        "合計": None,
        "二桁値引後計": None,
        "二桁値引": None,
        "表示順": 1,
        "順番": 1,
        "再発行": None,
        "削除": None,
        "暫定区分": None,
    }
    for extra in rows:
        row = {**base, **extra}
        ws.append([row[c] for c in EXPECTED_COLUMNS])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestSourceReadsDeletedFlag:
    """excel_reader が「削除」列を実際に読んでいること。"""

    def test_empty_deleted_column_is_not_deleted(self):
        content = _build_workbook([{"削除": None}])
        result = excel_reader.parse(io.BytesIO(content))
        assert not result.has_error, [i.message for i in result.errors]
        assert result.rows[0].is_deleted_in_source is False

    def test_nonempty_deleted_column_is_deleted(self):
        content = _build_workbook([{"削除": "済"}])
        result = excel_reader.parse(io.BytesIO(content))
        assert result.rows[0].is_deleted_in_source is True

    def test_deleted_row_still_has_correct_amount_fields(self):
        """削除フラグが立っていても、他のフィールドは通常どおり読める。

        行自体は保持する方針（値引行と同じ扱い）のため。
        """
        content = _build_workbook([{"削除": "済", "小計": 5000}])
        result = excel_reader.parse(io.BytesIO(content))
        row = result.rows[0]
        assert row.source_amount == Decimal("5000")
        assert row.unit_price == Decimal("1000")


class TestImportSetsLogicalDeletion:
    """importer が deleted_at を実際に設定し、集計から除外されること。"""

    def test_deleted_row_gets_deleted_at(self, db):
        content = _build_workbook(
            [{"削除": "済"}, {"削除": None, "識別番号": "T2", "受注番号": "ORDER2"}]
        )
        result = excel_reader.parse(io.BytesIO(content))
        assert not result.has_error, [i.message for i in result.errors]

        user = AppUser(login_id="pytest2", display_name="t", role=UserRole.APPROVER)
        db.add(user)
        db.flush()

        summary = importer.run_import(db, result, "test.xlsx", user)
        db.flush()

        assert summary.deleted_in_source == 1

        lines = (
            db.query(BillingLine)
            .filter(BillingLine.period_id == summary.period_id)
            .order_by(BillingLine.id)
            .all()
        )
        assert len(lines) == 2
        deleted = [line for line in lines if line.deleted_at is not None]
        alive = [line for line in lines if line.deleted_at is None]
        assert len(deleted) == 1
        assert len(alive) == 1

    def test_deleted_row_excluded_from_active_query(self, db):
        """deleted_at IS NULL で絞る本番の集計パターンと同じ形で検証する。"""
        from sqlalchemy import select

        content = _build_workbook(
            [{"削除": "済", "小計": 99999}],
            period_start=dt.date(2099, 2, 1),
            period_end=dt.date(2099, 2, 28),
        )
        result = excel_reader.parse(io.BytesIO(content))
        assert not result.has_error, [i.message for i in result.errors]

        user = AppUser(login_id="pytest3", display_name="t", role=UserRole.APPROVER)
        db.add(user)
        db.flush()

        summary = importer.run_import(db, result, "test.xlsx", user)
        db.flush()

        active_rows = db.execute(
            select(BillingLine).where(
                BillingLine.period_id == summary.period_id,
                BillingLine.deleted_at.is_(None),
            )
        ).scalars().all()
        assert len(active_rows) == 0, "削除された明細が請求対象の集計に混入している"
