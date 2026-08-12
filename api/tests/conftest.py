"""テスト共通の準備。

各テストはトランザクション内で実行し、終了時にロールバックする。
DBに書き込みが残らないので、何度流しても結果が変わらない。
"""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.db import engine
from app.models import (
    BillingPeriod,
    Client,
    Contract,
    Customer,
    Item,
    Office,
    RentalOrder,
    SalesRep,
    Site,
    TaxCategory,
)


@pytest.fixture
def db():
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


@pytest.fixture
def fixture_graph(db: Session) -> dict:
    """明細行を1本作るのに最低限必要なマスタと取引の親を用意する。"""
    office = Office(code="7104600", name="テスト営業所")
    customer = Customer(code="710429001", name="架空事務機株式会社")
    client = Client(name="サンプル工業株式会社", normalized_name="サンプル工業")
    rep = SalesRep(code="900001", name="テスト担当")
    db.add_all([office, customer, client, rep])
    db.flush()

    site = Site(
        name="サンプル工業株式会社　第02サンプル改修工事",
        address="東京都サンプル区",
        client_id=client.id,
    )
    db.add(site)
    db.flush()

    contract = Contract(
        contract_no="7000000000002",
        customer_id=customer.id,
        site_id=site.id,
        sales_rep_id=rep.id,
        office_id=office.id,
    )
    item = Item(code="DM-001", name="テスト品目", tax_category=TaxCategory.STANDARD)
    period = BillingPeriod(start_date=date(2025, 3, 1), end_date=date(2025, 3, 31))
    db.add_all([contract, item, period])
    db.flush()

    order = RentalOrder(
        period_id=period.id,
        contract_id=contract.id,
        order_no="700000000000201",
    )
    db.add(order)
    db.flush()

    return {
        "period": period,
        "contract": contract,
        "item": item,
        "order": order,
        "site": site,
        "client": client,
        "customer": customer,
    }
