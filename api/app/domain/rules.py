"""VBAの判定ロジックを移植した純粋関数。

ここに集約する理由は、VBA では同じ判定が複数モジュールにコピーされていて
（会社名の正規化は3か所、品番の判定は8か所）、片方だけ直されて不整合が
起きていたため。1か所に置いてテストで固定する。

VBAソースの該当箇所は docs/vba-analysis.md 3章・4章を参照。
"""

from decimal import Decimal, InvalidOperation

from app.models.base import BillingGroup, TaxCategory, UnitPriceType

# ---------------------------------------------------------------------------
# 判定文字列（全角）
#
# VBA から直接コピーせず、コードポイントで組み立てる。
# ＳＲ－ＷＰＥＴ のハイフンは全角 U+FF0D で、半角 '-' では一致しない。
# エディタや貼り付けで半角に化けても、ここなら壊れない。
# ---------------------------------------------------------------------------
WATER_BOTTLE_CODE = "".join(
    chr(c) for c in (0xFF33, 0xFF32, 0xFF0D, 0xFF37, 0xFF30, 0xFF25, 0xFF34)
)  # ＳＲ－ＷＰＥＴ
COUNTER_CODE_FRAGMENT = "".join(chr(c) for c in (0xFF30, 0xFF23, 0xFF21))  # ＰＣＡ
DISCOUNT_KEYWORD = "値引"

IDEOGRAPHIC_SPACE = "　"  # 全角スペース
CORP_SUFFIX_PAREN = "（株）"
CORP_SUFFIX_LIGATURE = "㈱"
TENTATIVE_NAME = "（仮称）"


# ---------------------------------------------------------------------------
# 品目の分類。取込時に1度だけ判定し、以降はマスタの列を見る
# ---------------------------------------------------------------------------


def classify_tax_category(item_code: str) -> str:
    """税区分。品番が ＳＲ－ＷＰＥＴ（飲料水）なら軽減税率8%。

    VBA: If SourceData(r, 21) = "ＳＲ－ＷＰＥＴ"  （完全一致）
    """
    return (
        TaxCategory.REDUCED
        if (item_code or "") == WATER_BOTTLE_CODE
        else TaxCategory.STANDARD
    )


def classify_billing_group(item_code: str) -> str:
    """請求グループ。品番に ＰＣＡ を含むものはカウンタ類で、明細書を分ける。

    VBA: If PrdCode Like "*ＰＣＡ*"  （部分一致）
    """
    return (
        BillingGroup.COUNTER
        if COUNTER_CODE_FRAGMENT in (item_code or "")
        else BillingGroup.EQUIPMENT
    )


def is_billable_item(item_name: str) -> bool:
    """品名に「値引」を含む行は請求対象外。

    VBA: If Not PrdName Like "*値引*"  （この条件のとき転記する）
    行は削除せず is_billable=False で残し、集計から除外する。
    """
    return DISCOUNT_KEYWORD not in (item_name or "")


# ---------------------------------------------------------------------------
# 会社名の名寄せ
# ---------------------------------------------------------------------------


def normalize_client_name(site_name: str) -> str:
    """納品先名称から得意先（会社名）を取り出す。

    VBA の ListTable.リスト表作成 / companyNameList.CreateCompanyList を移植。
    両者は書き方が違うが結果は同じ。

        「サンプル工業株式会社　第02サンプル改修工事」 → 「サンプル工業株式会社」

    VBA の InStr は1始まり・未検出0、Python の find は0始まり・未検出-1。
    条件の対応は次のとおり。

        VBA  If pos > 0   （検出した）        → Python  if p >= 0
        VBA  If pos > 1   （先頭以外で検出）  → Python  if p > 0

    （仮称）だけ pos > 1 なのは「先頭に来ている場合は残す」という
    VBA のコメントどおりの意図的な差。
    """
    s = str(site_name or "")

    # 全角スペースの前までを会社名とみなす
    p = s.find(IDEOGRAPHIC_SPACE)
    if p >= 0:
        s = s[:p]

    # （株）と㈱を同一視して除去する
    s = s.replace(CORP_SUFFIX_PAREN, CORP_SUFFIX_LIGATURE)

    # 「（仮称）」以降を落とす。ただし先頭にある場合は残す
    p = s.find(TENTATIVE_NAME)
    if p > 0:
        s = s[:p]

    s = s.replace(CORP_SUFFIX_LIGATURE, "")

    return s.strip()


# ---------------------------------------------------------------------------
# 単価の判別
# ---------------------------------------------------------------------------


def resolve_unit_price(
    monthly_rate: Decimal | None,
    rental_rate: Decimal | None,
    monthly_conversion: Decimal | None,
    elapsed_days: Decimal | None,
    sale_price: Decimal | None,
) -> tuple[str, Decimal | None, Decimal | None]:
    """単価種別・単価・期間を決める。戻り値は (種別, 単価, 期間)。

    VBA（invoice10.bas）:

        If IsEmpty(MonthlyRate) Then              ' AI列 月単価
            If SourceData(r, 38) <> "" Then       ' AL列 レンタル単価
                単価 = SourceData(r, 38)
                期間 = SourceData(r, 30)          ' AD列 経過日数
            Else
                単価 = 販売単価                    ' AK列
                期間 = なし
            End If
        Else
            単価 = MonthlyRate
            期間 = SourceData(r, 31)              ' AE列 月換算
        End If

    存在判定に「日単価(AJ)」ではなく「レンタル単価(AL)」を使うのは VBA の実装どおり。
    実データ937行では AL列 は月単価・日単価の写しで全行一致しているため差は出ないが、
    将来データで食い違ったときに VBA と同じ結果を出すため、あえて AL列 を見る。
    """
    if monthly_rate is not None:
        return UnitPriceType.MONTHLY, monthly_rate, monthly_conversion
    if rental_rate is not None:
        return UnitPriceType.DAILY, rental_rate, elapsed_days
    return UnitPriceType.SALE, sale_price, None


def normalize_base_charge(base_charge: Decimal | None) -> Decimal | None:
    """基本料が 0 のときは空として扱う。

    VBA: If BasicCharge = 0 Then dataArr(7) = "" Else dataArr(7) = BasicCharge

    金額計算では COALESCE(base_charge, 0) となるため結果は変わらないが、
    明細書に「0」と印字させないための現行踏襲。
    """
    if base_charge is None or base_charge == 0:
        return None
    return base_charge


# ---------------------------------------------------------------------------
# 値の変換
# ---------------------------------------------------------------------------


def to_decimal(value: object) -> Decimal | None:
    """Excelの値を Decimal に変換する。float を経由させない。

    openpyxl は数値を int / float で返すため、str() を挟んでから Decimal にする。
    Decimal(float) だと 33.0 が Decimal('33.00000000000000142...') になりうる。
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        v = value.strip().replace(",", "")
        if not v:
            return None
        try:
            return Decimal(v)
        except InvalidOperation:
            return None
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def to_text(value: object) -> str | None:
    """空文字は None に寄せる。Excelの空セルと空文字を区別しない。"""
    if value is None:
        return None
    s = str(value).strip()
    return s or None
