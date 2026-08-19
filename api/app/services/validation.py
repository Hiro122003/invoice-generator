"""F-07 発行前チェック。

VBAの `明細数の確認()`（リスト表にあるが明細書にない契約番号をDebug.Printで
出すだけの約60行）を、発行前バリデーションとして正式な機能に格上げする
（docs/domain-model.md 6章）。ただしVBA原本は10%シートしか見ておらず、
8%側は素通りだった。ここでは税率を問わず両方チェックする（意図的な改善）。

`リスト表と営業データの差分()`（VBA）は「前月比較」ではなく、リスト表と
営業部が別途持つ見込みデータ（同じシートに手で貼り付け）との突き合わせで、
そもそも今回のドメインモデルに存在しないデータソースを前提にしていた。
ここでの「前月比」はユーザーの了承のもと、字義通りの意味で作り直す:
**直前の請求期間とDB上で比較する**。追加の取込機構は不要。

4種類のチェックはすべて独立した観点。
  1. 請求漏れ   MISSING_STATEMENT  契約に請求対象の明細行があるのに、
                                    どの明細書にも割り当てられていない
  2. 金額0円    ZERO_AMOUNT        請求対象の明細行なのに amount が0円
  3. 期間外     OUT_OF_PERIOD      明細行のレンタル期間が請求期間と重ならない
  4. 前月比     NEW_CONTRACT / VANISHED_CONTRACT / AMOUNT_CHANGED
                                    契約単位で前期間と比較

前月比の「急増急減」の閾値（金額差1万円以上 かつ 変化率30%以上）はVBAに
原典がない、この機能のために新設した閾値。将来チューニングの余地がある
ことをコードコメントに明示しておく。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import BillingPeriod


class PeriodNotFoundError(Exception):
    pass


class CheckCategory:
    MISSING_STATEMENT = "MISSING_STATEMENT"
    ZERO_AMOUNT = "ZERO_AMOUNT"
    OUT_OF_PERIOD = "OUT_OF_PERIOD"
    NEW_CONTRACT = "NEW_CONTRACT"
    VANISHED_CONTRACT = "VANISHED_CONTRACT"
    AMOUNT_CHANGED = "AMOUNT_CHANGED"


class Severity:
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    INFO = "INFO"


# 前月比「急増急減」の閾値。両方満たしたときだけ報告する
# （金額の小さい契約が誤差レベルで変動するたびに報告されるのを防ぐ）。
_AMOUNT_CHANGE_MIN_DIFF = Decimal("10000")
_AMOUNT_CHANGE_MIN_RATE = Decimal("0.3")


@dataclass
class ValidationIssue:
    category: str
    severity: str
    message: str
    contract_id: int | None = None
    contract_no: str | None = None
    client_name: str | None = None
    site_name: str | None = None
    item_code: str | None = None
    item_name: str | None = None
    amount: Decimal | None = None
    previous_amount: Decimal | None = None


@dataclass
class ValidationResult:
    period_id: int
    previous_period_id: int | None
    previous_period_label: str | None
    issues: list[ValidationIssue] = field(default_factory=list)


# ---------------------------------------------------------------------
# 1. 請求漏れ
# ---------------------------------------------------------------------

_MISSING_STATEMENT_SQL = text(
    """
    SELECT c.id AS contract_id, c.contract_no, cl.name AS client_name,
           s.name AS site_name,
           COUNT(*) AS missing_count,
           SUM(bl.amount) AS missing_amount
      FROM billing_line bl
      JOIN rental_order o ON o.id = bl.order_id
      JOIN contract c ON c.id = o.contract_id
      JOIN site s ON s.id = c.site_id
      JOIN client cl ON cl.id = s.client_id
     WHERE bl.period_id = :period_id
       AND bl.deleted_at IS NULL
       AND bl.is_billable
       AND NOT c.skip_statement
       AND bl.statement_id IS NULL
     GROUP BY c.id, c.contract_no, cl.name, s.name
     ORDER BY c.contract_no
    """
)


def _check_missing_statements(db: Session, period_id: int) -> list[ValidationIssue]:
    rows = db.execute(_MISSING_STATEMENT_SQL, {"period_id": period_id}).mappings().all()
    return [
        ValidationIssue(
            category=CheckCategory.MISSING_STATEMENT,
            severity=Severity.HIGH,
            contract_id=r["contract_id"],
            contract_no=r["contract_no"],
            client_name=r["client_name"],
            site_name=r["site_name"],
            amount=r["missing_amount"],
            message=(
                f"請求対象の明細が{r['missing_count']}件"
                f"（税抜{r['missing_amount']}円）明細書に含まれていません。"
                "「請求書」ページで生成し直してください。"
            ),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------
# 2. 金額0円
# ---------------------------------------------------------------------

_ZERO_AMOUNT_SQL = text(
    """
    SELECT bl.id AS line_id, c.id AS contract_id, c.contract_no,
           cl.name AS client_name, s.name AS site_name,
           i.code AS item_code, bl.item_name_snapshot AS item_name
      FROM billing_line bl
      JOIN rental_order o ON o.id = bl.order_id
      JOIN contract c ON c.id = o.contract_id
      JOIN site s ON s.id = c.site_id
      JOIN client cl ON cl.id = s.client_id
      JOIN item i ON i.id = bl.item_id
     WHERE bl.period_id = :period_id
       AND bl.deleted_at IS NULL
       AND bl.is_billable
       AND NOT c.skip_statement
       AND bl.amount = 0
     ORDER BY c.contract_no, bl.id
    """
)


def _check_zero_amount(db: Session, period_id: int) -> list[ValidationIssue]:
    rows = db.execute(_ZERO_AMOUNT_SQL, {"period_id": period_id}).mappings().all()
    return [
        ValidationIssue(
            category=CheckCategory.ZERO_AMOUNT,
            severity=Severity.MEDIUM,
            contract_id=r["contract_id"],
            contract_no=r["contract_no"],
            client_name=r["client_name"],
            site_name=r["site_name"],
            item_code=r["item_code"],
            item_name=r["item_name"],
            amount=Decimal("0"),
            message=f"「{r['item_name']}」が0円で請求されます。基本料・単価の設定を確認してください。",
        )
        for r in rows
    ]


# ---------------------------------------------------------------------
# 3. 期間外
# ---------------------------------------------------------------------

_OUT_OF_PERIOD_SQL = text(
    """
    SELECT bl.id AS line_id, c.id AS contract_id, c.contract_no,
           cl.name AS client_name, s.name AS site_name,
           i.code AS item_code, bl.item_name_snapshot AS item_name,
           bl.rental_start, bl.rental_end
      FROM billing_line bl
      JOIN rental_order o ON o.id = bl.order_id
      JOIN contract c ON c.id = o.contract_id
      JOIN site s ON s.id = c.site_id
      JOIN client cl ON cl.id = s.client_id
      JOIN item i ON i.id = bl.item_id
     WHERE bl.period_id = :period_id
       AND bl.deleted_at IS NULL
       AND bl.is_billable
       AND NOT c.skip_statement
       AND bl.rental_start IS NOT NULL
       AND bl.rental_end IS NOT NULL
       AND (bl.rental_end < :period_start OR bl.rental_start > :period_end)
     ORDER BY c.contract_no, bl.id
    """
)


def _check_out_of_period(
    db: Session, period_id: int, period: BillingPeriod
) -> list[ValidationIssue]:
    rows = db.execute(
        _OUT_OF_PERIOD_SQL,
        {
            "period_id": period_id,
            "period_start": period.start_date,
            "period_end": period.end_date,
        },
    ).mappings().all()
    return [
        ValidationIssue(
            category=CheckCategory.OUT_OF_PERIOD,
            severity=Severity.MEDIUM,
            contract_id=r["contract_id"],
            contract_no=r["contract_no"],
            client_name=r["client_name"],
            site_name=r["site_name"],
            item_code=r["item_code"],
            item_name=r["item_name"],
            message=(
                f"「{r['item_name']}」のレンタル期間"
                f"（{r['rental_start']}〜{r['rental_end']}）が"
                f"請求期間（{period.start_date}〜{period.end_date}）と重なりません。"
            ),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------
# 4. 前月比
# ---------------------------------------------------------------------

_PREVIOUS_PERIOD_SQL = text(
    """
    SELECT id, start_date, end_date
      FROM billing_period
     WHERE start_date < :this_start
     ORDER BY start_date DESC
     LIMIT 1
    """
)

# is_billable かつ skip_statement でない契約だけを対象にする。
# generator.generate() が明細書を作る対象と同じ条件に揃えることで、
# 「請求されるはずの金額」同士を比較する。
_PERIOD_CONTRACT_TOTALS_SQL = """
    SELECT o.contract_id, SUM(bl.amount) AS total_ex_tax
      FROM billing_line bl
      JOIN rental_order o ON o.id = bl.order_id
      JOIN contract c ON c.id = o.contract_id
     WHERE bl.period_id = :period_id
       AND bl.deleted_at IS NULL
       AND bl.is_billable
       AND NOT c.skip_statement
     GROUP BY o.contract_id
"""

_MONTH_OVER_MONTH_SQL = text(
    f"""
    WITH cur AS ({_PERIOD_CONTRACT_TOTALS_SQL}),
    prev AS (
        {_PERIOD_CONTRACT_TOTALS_SQL.replace(":period_id", ":prev_period_id")}
    ),
    combined AS (
        SELECT COALESCE(cur.contract_id, prev.contract_id) AS contract_id,
               cur.total_ex_tax  AS cur_amount,
               prev.total_ex_tax AS prev_amount
          FROM cur
          FULL OUTER JOIN prev ON cur.contract_id = prev.contract_id
    )
    SELECT co.contract_id, c.contract_no, cl.name AS client_name, s.name AS site_name,
           co.cur_amount, co.prev_amount
      FROM combined co
      JOIN contract c ON c.id = co.contract_id
      JOIN site s ON s.id = c.site_id
      JOIN client cl ON cl.id = s.client_id
     WHERE co.cur_amount IS DISTINCT FROM co.prev_amount
     ORDER BY c.contract_no
    """
)


def _check_month_over_month(
    db: Session, period_id: int, period: BillingPeriod
) -> tuple[list[ValidationIssue], int | None, str | None]:
    prev = db.execute(
        _PREVIOUS_PERIOD_SQL, {"this_start": period.start_date}
    ).mappings().first()
    if prev is None:
        return [], None, None

    prev_period_id = prev["id"]
    prev_label = prev["start_date"].strftime("%Y-%m")

    rows = db.execute(
        _MONTH_OVER_MONTH_SQL,
        {"period_id": period_id, "prev_period_id": prev_period_id},
    ).mappings().all()

    issues: list[ValidationIssue] = []
    for r in rows:
        cur_amount = r["cur_amount"]
        prev_amount = r["prev_amount"]
        common = dict(
            contract_id=r["contract_id"],
            contract_no=r["contract_no"],
            client_name=r["client_name"],
            site_name=r["site_name"],
        )

        if prev_amount is None:
            issues.append(
                ValidationIssue(
                    category=CheckCategory.NEW_CONTRACT,
                    severity=Severity.INFO,
                    amount=cur_amount,
                    message=f"{prev_label}になかった契約です（今期税抜{cur_amount}円）。",
                    **common,
                )
            )
            continue

        if cur_amount is None:
            issues.append(
                ValidationIssue(
                    category=CheckCategory.VANISHED_CONTRACT,
                    severity=Severity.INFO,
                    previous_amount=prev_amount,
                    message=f"{prev_label}にあった契約が今期は請求対象にありません（前期税抜{prev_amount}円）。",
                    **common,
                )
            )
            continue

        diff = cur_amount - prev_amount
        rate = abs(diff) / prev_amount if prev_amount != 0 else None
        large_enough = abs(diff) >= _AMOUNT_CHANGE_MIN_DIFF
        rate_enough = prev_amount == 0 or (rate is not None and rate >= _AMOUNT_CHANGE_MIN_RATE)
        if large_enough and rate_enough:
            direction = "増加" if diff > 0 else "減少"
            issues.append(
                ValidationIssue(
                    category=CheckCategory.AMOUNT_CHANGED,
                    severity=Severity.MEDIUM,
                    amount=cur_amount,
                    previous_amount=prev_amount,
                    message=(
                        f"{prev_label}比で税抜金額が{direction}しています"
                        f"（{prev_amount}円 → {cur_amount}円）。"
                    ),
                    **common,
                )
            )

    return issues, prev_period_id, prev_label


# ---------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------


def validate_period(db: Session, period_id: int) -> ValidationResult:
    period = db.get(BillingPeriod, period_id)
    if period is None:
        raise PeriodNotFoundError(f"請求期間が見つかりません: {period_id}")

    # 他の期間ロック処理（generate/confirm/PATCH/import/export）と同じ
    # ロックキー。読むだけの処理だが、複数のSELECTにまたがるチェックの
    # 途中で並行して生成・手修正が走ると、チェック結果が一つの瞬間の
    # スナップショットにならない（このプロジェクトで指摘済みの
    # check-then-act 系事故と同じ構造）。取得しておけば、進行中の
    # 生成・確定・手修正の完了を待ってから一貫した状態を読める。
    db.execute(text("SELECT pg_advisory_xact_lock(:period_id)"), {"period_id": period_id})

    issues: list[ValidationIssue] = []
    issues.extend(_check_missing_statements(db, period_id))
    issues.extend(_check_zero_amount(db, period_id))
    issues.extend(_check_out_of_period(db, period_id, period))

    mom_issues, prev_period_id, prev_label = _check_month_over_month(db, period_id, period)
    issues.extend(mom_issues)

    return ValidationResult(
        period_id=period_id,
        previous_period_id=prev_period_id,
        previous_period_label=prev_label,
        issues=issues,
    )
