"""F-09 PDF出力。帳票のHTMLを組み立てる（Playwright非依存）。

Playwrightを起動しなくてもテストできるよう、HTML文字列を作るところと
実際にPDF化するところ（pdf.py）を分けている。

紙面レイアウトはP-05（明細書詳細）・P-06（請求書）の画面と同じ項目構成
だが、Next.jsの画面をそのまま流用するのではなく、ここで独立したHTMLを
組み立てる（アーキテクチャ上、PDF生成はFastAPI側の責務。docs/design.md
7章）。VBAの「44行オフセット」計算（`docs/vba-analysis.md` 6章）は
ページ数に応じたセル位置の手計算がすべて不要になり、
`page-break-inside: avoid` 等のCSSに置き換わる。

税額・合計金額そのものはここでは一切計算しない（呼び出し側のexport.pyが
compute_invoice_totals/_STATEMENT_LIST_SQL で確定させた値をそのまま
受け取るだけ）。ただし手修正（F-05）で単価に小数を入れることは
スキーマ上可能なため、円未満の端数を持つ金額を「1円単位の表示文字列」に
丸める処理そのものは避けられない。Pythonのf"{d:,.0f}"はDecimalの既定の
丸め（ROUND_HALF_EVEN、銀行丸め）に委ねてしまい、画面側
（web/lib/api.ts の formatYen、JSのtoLocaleStringは半分を切り上げ側へ
丸める）と結果がずれることがあった（money-auditで実測: 1234.50が
PDF側"1,234"・画面側"1,235"に食い違う）。_yen() は明示的に
ROUND_HALF_UPで丸め、画面側と一致させる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

TAX_LABEL = {"STANDARD": "10%", "REDUCED": "8%"}
GROUP_LABEL = {"EQUIPMENT": "備品", "COUNTER": "カウンタ"}
UNIT_PRICE_TYPE_LABEL = {"MONTHLY": "月極", "DAILY": "日極", "SALE": "売切"}


def _yen(value: Decimal | int | None) -> str:
    if value is None:
        return "—"
    # 1円未満は四捨五入（半分は切り上げ側）。画面側のformatYen
    # （JSのtoLocaleString）と丸め方向を揃える。既定のDecimal書式
    # （ROUND_HALF_EVEN）に任せない。
    d = Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{d:,}"


def _num(value: Decimal | None) -> str:
    if value is None:
        return "—"
    d = Decimal(value)
    if d == d.to_integral_value():
        return f"{d:,.0f}"
    return f"{d:,}"


@dataclass
class StatementLineDoc:
    delivery_date: str | None
    item_code: str
    item_name: str
    quantity: Decimal
    base_charge: Decimal | None
    unit_price: Decimal | None
    duration: Decimal | None
    unit_price_type: str
    amount: Decimal


@dataclass
class StatementDoc:
    id: int
    contract_no: str
    client_name: str
    site_name: str
    billing_group: str
    total_ex_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    lines: list[StatementLineDoc] = field(default_factory=list)


@dataclass
class InvoiceSummaryRow:
    contract_no: str
    site_name: str
    billing_group: str
    total_ex_tax: Decimal
    total_amount: Decimal


@dataclass
class InvoiceDoc:
    id: int
    customer_name: str
    tax_category: str
    tax_rate: Decimal
    revision: int
    total_ex_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    rows: list[InvoiceSummaryRow] = field(default_factory=list)


_BASE_CSS = """
@page { size: A4; margin: 14mm 12mm; }
* { box-sizing: border-box; }
body {
  font-family: "IPAGothic", "Noto Sans CJK JP", "Hiragino Sans", sans-serif;
  color: #1a1a1a;
  font-size: 11px;
  margin: 0;
}
.doc { page-break-after: always; }
.doc:last-child { page-break-after: auto; }
h1 { font-size: 18px; margin: 0 0 12px; letter-spacing: 0.15em; }
.meta { display: flex; flex-wrap: wrap; gap: 18px; margin-bottom: 10px; border: 1px solid #999; padding: 8px 12px; }
.meta div { min-width: 140px; }
.meta dt { font-size: 9px; color: #555; letter-spacing: 0.05em; }
.meta dd { margin: 2px 0 0; font-size: 12px; }
table { width: 100%; border-collapse: collapse; margin-bottom: 10px; }
th, td { border: 1px solid #999; padding: 3px 6px; font-size: 10px; }
th { background: #eee; text-align: center; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.totals { display: flex; justify-content: flex-end; gap: 22px; margin-top: 4px; }
.totals div { text-align: right; min-width: 90px; }
.totals dt { font-size: 9px; color: #555; }
.totals dd { margin: 2px 0 0; font-size: 13px; }
.totals .grand dd { font-size: 16px; font-weight: 700; }
.tag { display: inline-block; border: 1px solid #999; padding: 0 5px; font-size: 9px; }
"""


def render_statement_document_html(period_label: str, statements: list[StatementDoc]) -> str:
    """1つの請求書（税率）にぶら下がる明細書をすべて1つのPDFへ積む。

    VBAの「請求明細_10%/8%」シートが、ひな形44行をN枚縦に積んでいたのと
    同じ構成。ここではページ単位（.doc）に分けるだけで、行位置のオフセット
    計算は発生しない。
    """
    pages = []
    for st in statements:
        rows = "".join(
            f"""
            <tr>
              <td>{ln.delivery_date or "—"}</td>
              <td>{ln.item_code}</td>
              <td>{ln.item_name}</td>
              <td class="num">{_num(ln.quantity)}</td>
              <td class="num">{_yen(ln.base_charge) if ln.base_charge is not None else "—"}</td>
              <td class="num">{_yen(ln.unit_price) if ln.unit_price is not None else "—"}</td>
              <td>{UNIT_PRICE_TYPE_LABEL.get(ln.unit_price_type, ln.unit_price_type)}</td>
              <td class="num">{_num(ln.duration) if ln.duration is not None else "—"}</td>
              <td class="num">{_yen(ln.amount)}</td>
            </tr>
            """
            for ln in st.lines
        )
        pages.append(
            f"""
            <section class="doc">
              <h1>請求明細書</h1>
              <div class="meta">
                <div><dt>得意先</dt><dd>{st.client_name}</dd></div>
                <div><dt>管理番号</dt><dd>{st.contract_no}</dd></div>
                <div><dt>納品先</dt><dd>{st.site_name}</dd></div>
                <div><dt>請求期間</dt><dd>{period_label}</dd></div>
                <div><dt>区分</dt><dd><span class="tag">{GROUP_LABEL.get(st.billing_group, st.billing_group)}</span></dd></div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>納品日</th><th>品番</th><th>品名</th>
                    <th class="num">数量</th><th class="num">基本料</th><th class="num">単価</th>
                    <th>種別</th><th class="num">日数/月数</th><th class="num">金額</th>
                  </tr>
                </thead>
                <tbody>{rows}</tbody>
              </table>
              <div class="totals">
                <div><dt>税抜</dt><dd>{_yen(st.total_ex_tax)}</dd></div>
                <div><dt>消費税</dt><dd>{_yen(st.tax_amount)}</dd></div>
                <div class="grand"><dt>合計</dt><dd>{_yen(st.total_amount)}</dd></div>
              </div>
            </section>
            """
        )
    return f"<html><head><meta charset='utf-8'><style>{_BASE_CSS}</style></head><body>{''.join(pages)}</body></html>"


def render_invoice_document_html(period_label: str, invoice: InvoiceDoc) -> str:
    rows = "".join(
        f"""
        <tr>
          <td>{r.contract_no}</td>
          <td>{r.site_name}</td>
          <td><span class="tag">{GROUP_LABEL.get(r.billing_group, r.billing_group)}</span></td>
          <td class="num">{_yen(r.total_ex_tax)}</td>
          <td class="num">{_yen(r.total_amount)}</td>
        </tr>
        """
        for r in invoice.rows
    )
    return f"""
    <html><head><meta charset='utf-8'><style>{_BASE_CSS}</style></head><body>
    <section class="doc">
      <h1>請求書</h1>
      <div class="meta">
        <div><dt>宛先</dt><dd>{invoice.customer_name} 御中</dd></div>
        <div><dt>請求期間</dt><dd>{period_label}</dd></div>
        <div><dt>税率</dt><dd>{TAX_LABEL.get(invoice.tax_category, invoice.tax_category)}</dd></div>
        <div><dt>版数</dt><dd>第{invoice.revision}版</dd></div>
      </div>
      <table>
        <thead>
          <tr><th>契約番号</th><th>現場</th><th>区分</th><th class="num">税抜</th><th class="num">合計</th></tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="totals">
        <div><dt>税抜合計</dt><dd>{_yen(invoice.total_ex_tax)}</dd></div>
        <div><dt>消費税</dt><dd>{_yen(invoice.tax_amount)}</dd></div>
        <div class="grand"><dt>合計</dt><dd>{_yen(invoice.total_amount)}</dd></div>
      </div>
    </section>
    </body></html>
    """
