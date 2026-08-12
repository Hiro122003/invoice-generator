"""マスタ。洗い替えの対象外で、月次処理をまたいで存続する。"""

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BillingGroup, TaxCategory, TimestampMixin, UserRole


class Office(Base, TimestampMixin):
    """受注営業所。実データは1拠点のみだが、多拠点化に備えて表を分ける。"""

    __tablename__ = "office"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class Customer(Base, TimestampMixin):
    """販売先。請求書の宛先で、実データでは1社のみ。"""

    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_name: Mapped[str | None] = mapped_column(Text)
    # 請求書の宛名に付ける敬称（御中／様）
    honorific: Mapped[str] = mapped_column(Text, nullable=False, server_default="御中")


class Client(Base, TimestampMixin):
    """得意先。納品先名称から会社名だけを取り出して名寄せしたもの。

    VBA の CreateCompanyList() が文字列操作でやっていたことを、
    マスタとして持つ。表記ゆれは normalized_name で吸収する。
    """

    __tablename__ = "client"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # 全角スペース以降・（株）・㈱・（仮称）を除去した比較用の名称
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    sites: Mapped[list["Site"]] = relationship(back_populates="client")


class Site(Base, TimestampMixin):
    """納品先（現場）。1つの現場に契約が複数ぶら下がることがある。"""

    __tablename__ = "site"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    address: Mapped[str | None] = mapped_column(Text)
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("client.id"), nullable=False, index=True
    )

    client: Mapped[Client] = relationship(back_populates="sites")


class SalesRep(Base, TimestampMixin):
    """営業担当者。"""

    __tablename__ = "sales_rep"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class Item(Base, TimestampMixin):
    """品目。

    VBA が品番の文字列比較でやっていた3つの判定を、この表の列に持たせる。
    取込時に1度だけ判定し、以降のロジックからハードコードを追放する。

        If PrdCode = "ＳＲ－ＷＰＥＴ"   →  tax_category
        If PrdCode Like "*ＰＣＡ*"      →  billing_group
        If PrdName Like "*値引*"        →  is_billable
    """

    __tablename__ = "item"
    __table_args__ = (
        CheckConstraint(
            f"tax_category IN {TaxCategory.ALL}", name="tax_category"
        ),
        CheckConstraint(
            f"billing_group IN {BillingGroup.ALL}", name="billing_group"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    tax_category: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=TaxCategory.STANDARD, index=True
    )
    billing_group: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=BillingGroup.EQUIPMENT, index=True
    )
    # 品名に「値引」を含むものは請求対象外。行は残すが集計から除く
    is_billable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class Contract(Base, TimestampMixin):
    """契約。契約番号が業務上の自然キー。

    skip_statement は Excel に存在せず、利用者が画面で設定する情報。
    洗い替えで消してはいけない（消すと毎月付け直しになりVBA時代に戻る）。
    """

    __tablename__ = "contract"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_no: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customer.id"), nullable=False, index=True
    )
    site_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("site.id"), nullable=False, index=True
    )
    sales_rep_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sales_rep.id")
    )
    office_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("office.id"))

    # 明細不要。翌月以降も継承される
    skip_statement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", index=True
    )
    # 得意先別値引率。VBAでコメントアウトされていた要件の受け皿。今回は未使用
    discount_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    note: Mapped[str | None] = mapped_column(Text)

    customer: Mapped[Customer] = relationship()
    site: Mapped[Site] = relationship()
    sales_rep: Mapped[SalesRep | None] = relationship()


class AppUser(Base, TimestampMixin):
    """利用者。手修正・確定の実行者を記録するために必要。"""

    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint(f"role IN {UserRole.ALL}", name="role"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    login_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=UserRole.EDITOR
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
