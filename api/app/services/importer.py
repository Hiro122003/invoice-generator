"""読み取った明細をDBへ投入する。

洗い替えの範囲を誤ると過去月や運用設定が消える。この境界が本モジュールの肝。

    洗い替える   対象請求期間の billing_line / rental_order /
                 invoice / invoice_statement
    洗い替えない 他の請求期間、マスタ全般、issued_document、各種ログ

契約マスタの skip_statement（明細不要）は Excel に存在せず利用者が設定する。
消すと毎月付け直しになりVBA時代に戻るため、UPSERT では触らない。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models import (
    AppUser,
    BillingLine,
    BillingPeriod,
    Client,
    Contract,
    Customer,
    ImportBatch,
    Invoice,
    InvoiceStatement,
    Item,
    Office,
    PeriodStatus,
    RentalOrder,
    SalesRep,
    Site,
)
from app.services.excel_reader import ParseResult, SourceRow


class PeriodLockedError(Exception):
    """確定済みの請求期間に投入しようとした。"""


class ValidationFailedError(Exception):
    """検証エラーが残ったまま投入しようとした。"""


@dataclass
class ImportSummary:
    period_id: int
    period_start: dt.date
    period_end: dt.date
    inserted_lines: int
    orders: int
    contracts: int
    clients: int
    sites: int
    items: int
    new_items: list[str]
    deleted_lines: int  # 洗い替えで消えた「前回投入ぶん」の行数
    deleted_in_source: int  # 基幹システム側で削除マークが付き、論理削除で投入した行数


def find_unknown_item_codes(db: Session, result: ParseResult) -> list[str]:
    """品目マスタに未登録の品番を返す。取込前の警告に使う。

    新しい品番は税区分・請求グループの判定が正しいか人が見るべきなので、
    投入は許すが必ず知らせる。
    """
    codes = {r.item_code for r in result.rows}
    if not codes:
        return []
    known = set(
        db.execute(select(Item.code).where(Item.code.in_(codes))).scalars().all()
    )
    return sorted(codes - known)


def _get_or_create_period(
    db: Session, start: dt.date, end: dt.date
) -> BillingPeriod:
    period = db.execute(
        select(BillingPeriod).where(
            BillingPeriod.start_date == start, BillingPeriod.end_date == end
        )
    ).scalar_one_or_none()
    if period is None:
        period = BillingPeriod(start_date=start, end_date=end)
        db.add(period)
        db.flush()
    return period


def _upsert_masters(db: Session, rows: list[SourceRow]) -> dict:
    """マスタを整える。既存レコードは作り直さず、足りないものだけ足す。

    洗い替えの対象外なので、ここで作ったものは翌月以降も生き続ける。
    """
    # -- 受注営業所 --------------------------------------------------------
    offices: dict[str, Office] = {
        o.code: o for o in db.execute(select(Office)).scalars()
    }
    for r in rows:
        if r.office_code and r.office_code not in offices:
            o = Office(code=r.office_code, name=r.office_name or r.office_code)
            db.add(o)
            offices[r.office_code] = o

    # -- 販売先 ------------------------------------------------------------
    customers: dict[str, Customer] = {
        c.code: c for c in db.execute(select(Customer)).scalars()
    }
    for r in rows:
        if r.customer_code and r.customer_code not in customers:
            c = Customer(
                code=r.customer_code,
                name=r.customer_name or r.customer_code,
                contact_name=r.customer_contact,
            )
            db.add(c)
            customers[r.customer_code] = c

    # -- 営業担当者 --------------------------------------------------------
    reps: dict[str, SalesRep] = {
        s.code: s for s in db.execute(select(SalesRep)).scalars()
    }
    for r in rows:
        if r.sales_rep_code and r.sales_rep_code not in reps:
            s = SalesRep(code=r.sales_rep_code, name=r.sales_rep_name or r.sales_rep_code)
            db.add(s)
            reps[r.sales_rep_code] = s

    # -- 得意先（納品先名称から名寄せ）------------------------------------
    clients: dict[str, Client] = {
        c.name: c for c in db.execute(select(Client)).scalars()
    }
    for r in rows:
        if r.client_name and r.client_name not in clients:
            c = Client(name=r.client_name, normalized_name=r.client_name)
            db.add(c)
            clients[r.client_name] = c

    # -- 品目 --------------------------------------------------------------
    items: dict[str, Item] = {i.code: i for i in db.execute(select(Item)).scalars()}
    for r in rows:
        if r.item_code not in items:
            i = Item(
                code=r.item_code,
                name=r.item_name,
                tax_category=r.tax_category,
                billing_group=r.billing_group,
                is_billable=r.item_is_billable,
            )
            db.add(i)
            items[r.item_code] = i

    db.flush()

    # -- 納品先（現場）----------------------------------------------------
    sites: dict[str, Site] = {s.name: s for s in db.execute(select(Site)).scalars()}
    for r in rows:
        if r.site_name not in sites:
            s = Site(
                name=r.site_name,
                address=r.site_address,
                client_id=clients[r.client_name].id,
            )
            db.add(s)
            sites[r.site_name] = s
    db.flush()

    # -- 契約 --------------------------------------------------------------
    # skip_statement / discount_rate は既存値を保持する（利用者の設定のため）
    contracts: dict[str, Contract] = {
        c.contract_no: c for c in db.execute(select(Contract)).scalars()
    }
    for r in rows:
        if r.contract_no not in contracts:
            c = Contract(
                contract_no=r.contract_no,
                customer_id=customers[r.customer_code].id if r.customer_code else None,
                site_id=sites[r.site_name].id,
                sales_rep_id=reps[r.sales_rep_code].id if r.sales_rep_code else None,
                office_id=offices[r.office_code].id if r.office_code else None,
            )
            db.add(c)
            contracts[r.contract_no] = c
    db.flush()

    return {
        "offices": offices,
        "customers": customers,
        "reps": reps,
        "clients": clients,
        "items": items,
        "sites": sites,
        "contracts": contracts,
    }


def _wipe_period(db: Session, period_id: int) -> int:
    """対象請求期間の取引・出力データだけを消す。

    順序は子から。billing_line は invoice_statement を参照しているため、
    先に明細を消してから明細書・請求書を消す。

    マスタ・他期間・issued_document・各種ログには触れない。
    """
    deleted = db.execute(
        delete(BillingLine).where(BillingLine.period_id == period_id)
    ).rowcount

    statement_ids = select(InvoiceStatement.id).join(
        Invoice, Invoice.id == InvoiceStatement.invoice_id
    ).where(Invoice.period_id == period_id)
    db.execute(
        delete(InvoiceStatement).where(InvoiceStatement.id.in_(statement_ids))
    )
    db.execute(delete(Invoice).where(Invoice.period_id == period_id))
    db.execute(delete(RentalOrder).where(RentalOrder.period_id == period_id))
    db.flush()
    return deleted or 0


def run_import(
    db: Session,
    result: ParseResult,
    file_name: str,
    user: AppUser,
) -> ImportSummary:
    """洗い替え投入を実行する。呼び出し側でコミットすること。"""
    if result.has_error:
        raise ValidationFailedError(
            "、".join(i.message for i in result.errors)
        )
    if result.period_start is None or result.period_end is None:
        raise ValidationFailedError("請求期間が特定できません。")

    period = _get_or_create_period(db, result.period_start, result.period_end)

    # generator.generate() / confirmation.confirm_period 等と同じ
    # period_id のアドバイザリロックを取ってから確定状態を読む・洗い替える。
    # ロックなしだと確定処理とのcheck-then-actの隙間ができてしまう
    # （money-auditでconfirm/PATCH間の同種の事故を再現ずみ）。
    db.execute(text("SELECT pg_advisory_xact_lock(:period_id)"), {"period_id": period.id})

    if period.status == PeriodStatus.CONFIRMED:
        raise PeriodLockedError(
            f"{period.label} は確定済みです。再投入するには確定解除が必要です。"
        )

    new_items = find_unknown_item_codes(db, result)
    masters = _upsert_masters(db, result.rows)
    deleted = _wipe_period(db, period.id)

    # -- 受注 --------------------------------------------------------------
    orders: dict[str, RentalOrder] = {}
    for r in result.rows:
        if r.order_no in orders:
            continue
        o = RentalOrder(
            period_id=period.id,
            contract_id=masters["contracts"][r.contract_no].id,
            order_no=r.order_no,
            po_number_1=r.po_number_1,
            po_number_2=r.po_number_2,
        )
        db.add(o)
        orders[r.order_no] = o
    db.flush()

    # -- 明細行 ------------------------------------------------------------
    #
    # amount は生成列なので渡さない。DBが数量・単価・期間から計算する。
    # 取込時の値を src_* に控えておき、手修正との差分判定に使う。
    now = dt.datetime.now(dt.timezone.utc)
    deleted_in_source = 0
    for r in result.rows:
        item = masters["items"][r.item_code]
        # 基幹システム側で削除された行は、取込時点で論理削除しておく。
        # 集計クエリ（periods.py など）は deleted_at IS NULL で絞っているため、
        # ここで立てないと請求してはいけない行が金額に混入する。
        # 行自体は保持する（is_billable=false の値引行と同じ扱い方針）。
        line_deleted_at = now if r.is_deleted_in_source else None
        if r.is_deleted_in_source:
            deleted_in_source += 1
        db.add(
            BillingLine(
                period_id=period.id,
                order_id=orders[r.order_no].id,
                item_id=item.id,
                item_name_snapshot=r.item_name,
                delivery_date=r.delivery_date,
                return_date=r.return_date,
                shipped_date=r.shipped_date,
                rental_start=r.rental_start,
                rental_end=r.rental_end,
                unit=r.unit,
                note=r.note,
                quantity=r.quantity,
                base_charge=r.base_charge,
                unit_price=r.unit_price,
                duration=r.duration,
                unit_price_type=r.unit_price_type,
                src_quantity=r.quantity,
                src_base_charge=r.base_charge,
                src_unit_price=r.unit_price,
                src_duration=r.duration,
                # 品目マスタ側の判定を明細にも写す。集計はこちらを見る
                is_billable=item.is_billable,
                is_provisional=r.is_provisional,
                display_order=r.display_order,
                seq=r.seq,
                source_key=r.source_key,
                deleted_at=line_deleted_at,
            )
        )
    db.flush()

    warnings = [
        f"{i.type}: {i.message}" for i in result.warnings
    ]
    if new_items:
        warnings.append(f"UNKNOWN_ITEM: 新しい品番 {len(new_items)} 件を登録しました")
    if deleted_in_source:
        warnings.append(
            f"DELETED_IN_SOURCE: 基幹システム側で削除された明細 {deleted_in_source} 件を"
            "論理削除として取り込みました（請求対象外）"
        )

    db.add(
        ImportBatch(
            period_id=period.id,
            file_name=file_name,
            row_count=len(result.rows),
            warnings="\n".join(warnings) or None,
            imported_by=user.id,
        )
    )
    db.flush()

    return ImportSummary(
        period_id=period.id,
        period_start=period.start_date,
        period_end=period.end_date,
        inserted_lines=len(result.rows),
        orders=len(orders),
        contracts=len({r.contract_no for r in result.rows}),
        clients=len({r.client_name for r in result.rows}),
        sites=len({r.site_name for r in result.rows}),
        items=len({r.item_code for r in result.rows}),
        new_items=new_items,
        deleted_lines=deleted,
        deleted_in_source=deleted_in_source,
    )
