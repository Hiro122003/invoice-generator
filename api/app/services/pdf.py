"""F-09 PDF出力。HTML文字列をPlaywright（headless Chromium）でPDF化する。

ブラウザの起動はコストが高いため、1回のエクスポート処理（複数のHTMLを
PDF化する）で1つのブラウザインスタンスを使い回す。
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright


def render_pdfs(htmls: list[str]) -> list[bytes]:
    """複数のHTMLを、ブラウザ1つを使い回してPDF化する。"""
    results: list[bytes] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            try:
                for html in htmls:
                    page.set_content(html, wait_until="load")
                    results.append(page.pdf(format="A4", print_background=True))
            finally:
                page.close()
        finally:
            browser.close()
    return results


def render_pdf(html: str) -> bytes:
    return render_pdfs([html])[0]
