"""取引データ。請求期間ごとに洗い替えられる。"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PeriodStatus, TimestampMixin, UnitPriceType

if TYPE_CHECKING:
    from app.models.master import Contract


class BillingPeriod(Base, TimestampMixin):
    """請求期間。1か月分の処理単位であり、洗い替えの範囲を決める。"""

    __tablename__ = "billing_period"
    __table_args__ = (
        UniqueConstraint("start_date", "end_date", name="uq_billing_period_range"),
        CheckConstraint(f"status IN {PeriodStatus.ALL}", name="status"),
        CheckConstraint("end_date >= start_date", name="range_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=PeriodStatus.DRAFT, index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app_user.id")
    )

    @property
    def label(self) -> str:
        """画面表示用。2025-03 のような形。"""
        return self.start_date.strftime("%Y-%m")

    @property
    def is_locked(self) -> bool:
        return self.status == PeriodStatus.CONFIRMED


class RentalOrder(Base, TimestampMixin):
    """受注。1受注に明細が最大27行ぶら下がる（実データ実測）。

    洗い替えの対象。契約ごとに受注番号が振られる。
    """

    __tablename__ = "rental_order"
    __table_args__ = (
        UniqueConstraint("period_id", "order_no", name="uq_rental_order_period_no"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    period_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("billing_period.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contract_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contract.id"), nullable=False, index=True
    )
    order_no: Mapped[str] = mapped_column(Text, nullable=False)

    # 取引先発注番号。元データの3〜5は全行が空のため2つだけ持つ
    po_number_1: Mapped[str | None] = mapped_column(Text)
    po_number_2: Mapped[str | None] = mapped_column(Text)

    contract: Mapped["Contract"] = relationship()
    lines: Mapped[list["BillingLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class BillingLine(Base, TimestampMixin):
    """明細行。

    元データに自然キーがないためサロゲートキーを使う（識別番号は 851/937 しか
    ユニークでない）。差分更新は原理的に不可能なので、再取込は洗い替えのみ。

    金額 amount は生成列。アプリからは書き込めない。
    VBA が明細書のセルに数式を残していた設計思想をそのまま引き継ぐもので、
    「数量×単価と金額が食い違う行」がDB上に存在できなくなる。
    """

    __tablename__ = "billing_line"
    __table_args__ = (
        CheckConstraint(
            f"unit_price_type IN {UnitPriceType.ALL}", name="unit_price_type"
        ),
        # 集計は必ず「対象期間かつ論理削除でない」で絞るため部分インデックスにする。
        # postgresql_where には型ではなく SQL式（text）を渡すこと。
        Index(
            "ix_billing_line_period_active",
            "period_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # index は上の部分インデックス ix_billing_line_period_active が兼ねるため
    # ここでは張らない（同じ列に2本張っても書き込みが遅くなるだけ）
    period_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("billing_period.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rental_order.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("item.id"), nullable=False, index=True
    )
    # 明細書生成時に確定する。未生成なら NULL
    statement_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("invoice_statement.id", ondelete="SET NULL"),
        index=True,
    )

    # 発行時点の品名を凍結する。マスタの改称に過去の請求書が引きずられないため
    item_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)

    delivery_date: Mapped[date | None] = mapped_column(Date)
    return_date: Mapped[date | None] = mapped_column(Date)
    shipped_date: Mapped[date | None] = mapped_column(Date)
    rental_start: Mapped[date | None] = mapped_column(Date)
    rental_end: Mapped[date | None] = mapped_column(Date)
    unit: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)

    # ---- 手修正できる4項目 ------------------------------------------------
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    base_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # 日数 または 月換算。単価種別に応じて意味が変わる
    duration: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    unit_price_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=UnitPriceType.DAILY
    )

    # ---- 金額（生成列。アプリから書き込まない）---------------------------
    #
    #   VBA:  日数/月数あり  =(G*F)+(H*I*F)
    #         日数/月数なし  =(G*F)+(H*F)
    #         F=数量 G=基本料 H=単価 I=日数/月数
    #
    #   COALESCE(duration, 1) が上記2つの式の差を吸収して1本に統合する。
    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        Computed(
            "COALESCE(base_charge, 0) * quantity"
            " + COALESCE(unit_price, 0) * COALESCE(duration, 1) * quantity",
            persisted=True,
        ),
        nullable=False,
    )

    # ---- 取込時の値（手修正との比較用）-----------------------------------
    src_quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    src_base_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    src_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    src_duration: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    # 品名に「値引」を含む行は false。行は残して集計から除外する
    is_billable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", index=True
    )
    is_provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    display_order: Mapped[int | None] = mapped_column(Integer)
    seq: Mapped[int | None] = mapped_column(Integer)
    # 取込元の識別番号。追跡用で、一意ではない
    source_key: Mapped[str | None] = mapped_column(Text, index=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[RentalOrder] = relationship(back_populates="lines")

    @property
    def is_edited(self) -> bool:
        """取込時の値から変更されているか。保存せず都度導出する。"""
        return (
            self.quantity,
            self.base_charge,
            self.unit_price,
            self.duration,
        ) != (
            self.src_quantity,
            self.src_base_charge,
            self.src_unit_price,
            self.src_duration,
        )
