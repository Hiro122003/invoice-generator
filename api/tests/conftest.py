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
    """明細行を1本作るのに最低限必要なマスタと取引の親を用意する。

    コードや名称には接頭辞を付けて、開発用に投入済みの実データと
    一意制約でぶつからないようにする。請求期間も過去の架空月にする。
    """
    office = Office(code="TEST-OFFICE", name="テスト営業所")
    customer = Customer(code="TEST-CUSTOMER", name="テスト販売先株式会社")
    client = Client(name="テスト得意先株式会社", normalized_name="テスト得意先株式会社")
    rep = SalesRep(code="TEST-REP", name="テスト担当")
    db.add_all([office, customer, client, rep])
    db.flush()

    site = Site(
        name="テスト得意先株式会社　テスト現場",
        address="東京都テスト区",
        client_id=client.id,
    )
    db.add(site)
    db.flush()

    contract = Contract(
        contract_no="TEST-CONTRACT-0001",
        customer_id=customer.id,
        site_id=site.id,
        sales_rep_id=rep.id,
        office_id=office.id,
    )
    item = Item(code="TEST-ITEM", name="テスト品目", tax_category=TaxCategory.STANDARD)
    # 実運用ではありえない月にして、開発データの期間と衝突させない
    period = BillingPeriod(start_date=date(1990, 1, 1), end_date=date(1990, 1, 31))
    db.add_all([contract, item, period])
    db.flush()

    order = RentalOrder(
        period_id=period.id,
        contract_id=contract.id,
        order_no="TEST-ORDER-0001",
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
