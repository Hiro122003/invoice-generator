"""全モデルをここから import する。

Alembic の autogenerate は Base.metadata を見るため、
すべてのモデルがこのモジュール経由で読み込まれている必要がある。
"""

from app.models.audit import ImportBatch, LineEditLog, PeriodUnlockLog
from app.models.base import (
    Base,
    BillingGroup,
    DocType,
    InvoiceStatus,
    PeriodStatus,
    TaxCategory,
    UnitPriceType,
    UserRole,
)
from app.models.billing import BillingLine, BillingPeriod, RentalOrder
from app.models.invoice import Invoice, InvoiceStatement, IssuedDocument
from app.models.master import (
    AppUser,
    Client,
    Contract,
    Customer,
    Item,
    Office,
    SalesRep,
    Site,
)

__all__ = [
    "Base",
    # 列挙
    "TaxCategory",
    "BillingGroup",
    "UnitPriceType",
    "PeriodStatus",
    "InvoiceStatus",
    "DocType",
    "UserRole",
    # マスタ
    "Office",
    "Customer",
    "Client",
    "Site",
    "SalesRep",
    "Item",
    "Contract",
    "AppUser",
    # 取引
    "BillingPeriod",
    "RentalOrder",
    "BillingLine",
    # 出力
    "Invoice",
    "InvoiceStatement",
    "IssuedDocument",
    # 監査
    "ImportBatch",
    "LineEditLog",
    "PeriodUnlockLog",
]
