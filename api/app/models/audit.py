"""監査。取込・手修正・確定解除の記録。いずれも追記のみで削除しない。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ImportBatch(Base):
    """取込履歴。いつどのファイルを投入したか。"""

    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    period_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("billing_period.id"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 検証で出た警告をそのまま残す（JSON文字列）
    warnings: Mapped[str | None] = mapped_column(Text)

    imported_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id"), nullable=False
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LineEditLog(Base):
    """明細の手修正履歴。

    項目ごとに1レコード。1行の中で数量と単価を直せば2レコードになる。
    画面の「手修正 n件」は行単位で数えるので、粒度が違う点に注意。
    """

    __tablename__ = "line_edit_log"
    __table_args__ = (
        Index("ix_line_edit_log_line_time", "line_id", "edited_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    line_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("billing_line.id", ondelete="CASCADE"),
        nullable=False,
    )
    # quantity / base_charge / unit_price / duration
    field: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    new_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    reason: Mapped[str | None] = mapped_column(Text)

    edited_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id"), nullable=False
    )
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PeriodUnlockLog(Base):
    """確定解除の記録。理由の入力を必須とする。"""

    __tablename__ = "period_unlock_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    period_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("billing_period.id"), nullable=False, index=True
    )
    from_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    unlocked_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id"), nullable=False
    )
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
