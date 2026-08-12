"""モデルの基底クラスと共通定義。"""

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 制約名を機械的に決める。これがないと Alembic の autogenerate が
# 制約の変更を検出できず、マイグレーションが壊れる。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """作成・更新時刻。DB側で既定値を入れるため、アプリで設定し忘れても入る。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# ドメインの列挙値
#
# PostgreSQL の ENUM 型ではなく TEXT + CHECK 制約で表現する。
# ENUM 型は値の追加にマイグレーションが必要で、税率区分のように
# 将来増える可能性があるものには向かない。
# ---------------------------------------------------------------------------


class TaxCategory:
    """税区分。品番 ＳＲ－ＷＰＥＴ（飲料水）のみ軽減税率。"""

    STANDARD = "STANDARD"  # 10%
    REDUCED = "REDUCED"  # 8%
    ALL = (STANDARD, REDUCED)

    RATE = {STANDARD: "0.10", REDUCED: "0.08"}


class BillingGroup:
    """請求グループ。品番に ＰＣＡ を含むものは別の明細書に分ける。"""

    EQUIPMENT = "EQUIPMENT"  # 備品類
    COUNTER = "COUNTER"  # カウンタ類
    ALL = (EQUIPMENT, COUNTER)


class UnitPriceType:
    """単価の種別。元データの月単価・日単価・販売単価は相互排他。"""

    MONTHLY = "MONTHLY"  # 月単価 × 月換算
    DAILY = "DAILY"  # 日単価 × 経過日数
    SALE = "SALE"  # 販売単価（期間なし）
    ALL = (MONTHLY, DAILY, SALE)


class PeriodStatus:
    """請求期間の状態。確定済みは洗い替えも手修正も拒否する。"""

    DRAFT = "DRAFT"  # 取込済。再アップロードは無条件に洗い替え
    CONFIRMED = "CONFIRMED"  # 確定済。ロック
    ALL = (DRAFT, CONFIRMED)


class InvoiceStatus:
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"  # PDF発行済み
    SENT = "SENT"  # 先方へ送付済み
    PAID = "PAID"
    ALL = (DRAFT, ISSUED, SENT, PAID)


class DocType:
    INVOICE = "INVOICE"
    STATEMENT = "STATEMENT"
    BUNDLE_ZIP = "BUNDLE_ZIP"
    ALL = (INVOICE, STATEMENT, BUNDLE_ZIP)


class UserRole:
    VIEWER = "VIEWER"  # 閲覧のみ
    EDITOR = "EDITOR"  # 手修正まで
    APPROVER = "APPROVER"  # 確定・確定解除まで
    ALL = (VIEWER, EDITOR, APPROVER)
