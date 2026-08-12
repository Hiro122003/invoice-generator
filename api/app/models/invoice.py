"""出力。請求書・請求明細書と、発行したPDFの記録。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    BillingGroup,
    DocType,
    InvoiceStatus,
    TaxCategory,
    TimestampMixin,
)


class Invoice(Base, TimestampMixin):
    """請求書。請求期間 × 販売先 × 税率ごとに1通。

    消費税は「明細書の消費税の合計」ではなく「明細書の税抜の合計」から
    改めて切り上げる。両者は数円ずれるが、それが現行VBAの挙動。
    """

    __tablename__ = "invoice"
    __table_args__ = (
        UniqueConstraint(
            "period_id", "customer_id", "tax_category", name="uq_invoice_period_tax"
        ),
        CheckConstraint(f"tax_category IN {TaxCategory.ALL}", name="tax_category"),
        CheckConstraint(f"status IN {InvoiceStatus.ALL}", name="status"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    period_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("billing_period.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customer.id"), nullable=False
    )

    tax_category: Mapped[str] = mapped_column(Text, nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)

    # 確定のたびに +1。先方へ送った版を識別する
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # 確定時に書き込むスナップショット。確定前は NULL で、都度集計して求める
    total_ex_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=InvoiceStatus.DRAFT
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app_user.id")
    )
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    statements: Mapped[list["InvoiceStatement"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceStatement(Base, TimestampMixin):
    """請求明細書。契約 × 請求グループごとに1枚。

    billing_group を粒度に含めることで、VBA の分岐①②③
    （備品のみ／備品＋カウンタ／カウンタのみ）が
    「statement が1件か2件か」の違いに退化する。
    """

    __tablename__ = "invoice_statement"
    __table_args__ = (
        UniqueConstraint(
            "invoice_id",
            "contract_id",
            "billing_group",
            name="uq_invoice_statement_key",
        ),
        CheckConstraint(f"billing_group IN {BillingGroup.ALL}", name="billing_group"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("invoice.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contract_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contract.id"), nullable=False, index=True
    )
    billing_group: Mapped[str] = mapped_column(Text, nullable=False)

    # 確定時のスナップショット
    total_ex_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    sort_order: Mapped[int | None] = mapped_column(Integer)

    invoice: Mapped[Invoice] = relationship(back_populates="statements")


class IssuedDocument(Base):
    """発行済みPDF。

    「先方に何を送ったか」の正はDBの数値ではなくこのPDF。
    洗い替えの対象外で、削除もしない。
    """

    __tablename__ = "issued_document"
    __table_args__ = (CheckConstraint(f"doc_type IN {DocType.ALL}", name="doc_type"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    period_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("billing_period.id"), nullable=False, index=True
    )
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("invoice.id"))

    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)

    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int | None] = mapped_column(BigInteger)

    issued_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id"), nullable=False
    )
    # 文字列 "now()" を渡すと DEFAULT 'now()' という文字列リテラルになってしまう。
    # 関数呼び出しにするため func.now() を使う。
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
