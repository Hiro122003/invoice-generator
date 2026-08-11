---
name: vba-reference
description: 移行元VBAの実装を確認する。「VBAではどう実装していたか」「元の計算式は」「あの分岐の条件は」といった移植中の照会や、請求書_ver8(修正版).xlsm の中身・シート構成・セル配置を調べたいときに使う。.xlsm は通常のツールでは読めないため、この手順が必要。
---

# VBAソースの参照

移行元 `fixtures/請求書_ver8(修正版).xlsm` は Excel マクロブックで、Read/Grep では中身を読めない。
VBAコードもシート内容も、抽出してから読む。

## 前提

`oletools` と `openpyxl` が必要。未導入なら:

```bash
pip install oletools openpyxl
```

## 1. VBAソースを抽出する

抽出先はスクラッチパッド。リポジトリを汚さない。

```bash
cd "C:/Users/hiroy/Desktop/invoice-generator" && python -W ignore -c "
from oletools.olevba import VBA_Parser
p = VBA_Parser('fixtures/請求書_ver8(修正版).xlsm')
out = []
for (fn, sp, vn, code) in p.extract_macros():
    out.append('=' * 70)
    out.append('MODULE: ' + str(vn))
    out.append('=' * 70)
    out.append(code)
open('<SCRATCHPAD>/vba.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('ok')
"
```

以降は `vba.txt` を Read / Grep で普通に読める。約6,100行。

**注意**: 出力は必ずファイルへ書く。標準出力に出すと日本語が文字化けする（コンソールのコードページの都合）。
同じ理由で、確認結果もファイルに書いてから Read する。

## 2. モジュールの位置を把握する

```bash
grep -n "^MODULE:" vba.txt
grep -nE "^(Public |Private )?(Sub|Function) " vba.txt
```

主要モジュールは [docs/vba-analysis.md](../../../docs/vba-analysis.md) の表を参照。
`Bill8.bas` / `Bill10.bas` は**死にコード**なので、そこを読んで判断しないこと。
現役は `invoice10.bas` / `invoice8.bas` / `Bill10New.bas` / `Bill8New.bas`。

## 3. シートの内容・セル配置を見る

```bash
cd "C:/Users/hiroy/Desktop/invoice-generator" && python -W ignore -c "
import openpyxl, io
out = io.StringIO()
wb = openpyxl.load_workbook('fixtures/請求書_ver8(修正版).xlsm', data_only=True)
ws = wb['請求明細ひな形']            # ← シート名を変える
for r in range(1, 42):
    cells = [f'{openpyxl.utils.get_column_letter(c)}{r}={ws.cell(r,c).value!r}'
             for c in range(1, 11) if ws.cell(r,c).value not in (None, '')]
    if cells: print(' | '.join(cells), file=out)
open('<SCRATCHPAD>/sheet.txt', 'w', encoding='utf-8').write(out.getvalue())
print('ok')
"
```

`data_only=True` で値、`False` で数式が取れる。**明細書の数式を確認したいときは `False`。**

## 4. 図形・テキストボックスを見る

請求書のヘッダー（宛名・登録番号・振込先）はセルではなく図形に入っている。

```bash
cd "C:/Users/hiroy/Desktop/invoice-generator" && python -W ignore -c "
import zipfile, re, io
out = io.StringIO()
z = zipfile.ZipFile('fixtures/請求書_ver8(修正版).xlsm')
# drawing2=請求書8, drawing3=請求書10, drawing4=請求明細_10%, drawing5=請求明細_8%
t = z.read('xl/drawings/drawing3.xml').decode('utf-8')
for sp in re.findall(r'<xdr:sp .*?</xdr:sp>', t, re.S):
    nm = re.search(r'name=\"([^\"]*)\"', sp)
    txts = re.findall(r'<a:t>(.*?)</a:t>', sp, re.S)
    if txts: print(f'{nm.group(1) if nm else \"?\"} : {\"\".join(txts)!r}', file=out)
open('<SCRATCHPAD>/shapes.txt', 'w', encoding='utf-8').write(out.getvalue())
print('ok')
"
```

## よく参照される箇所

| 知りたいこと | 探す場所 |
|---|---|
| 明細行の金額式 | `grep -n 'formulaList(formulaCount) = "='` |
| 税抜・消費税・合計の式 | `grep -n 'Formula = "=SUM(J\|Formula = "=ROUNDUP(J'` |
| 請求書側の税計算 | `grep -n "RoundUp(beforeTax"` |
| 分岐①②③の条件 | `grep -n "MatchingDataForCounter"` |
| 品番の判定 | `grep -n "ＳＲ－ＷＰＥＴ\|ＰＣＡ"` |
| 会社名の正規化 | `grep -n "InStr(.*　\|（仮称）\|㈱"` |
| 単価の優先順位 | `grep -n "IsEmpty(MonthlyRate)"` |

## 判定文字列は全角

```
ＳＲ－ＷＰＥＴ   U+FF33 FF32 FF0D FF37 FF30 FF25 FF34   ← ハイフンは全角 U+FF0D
ＰＣＡ           U+FF30 FF23 FF21
```

半角で書くと一致しない。コードに写すときは VBAソースから直接コピーするか、
コードポイントで組み立てる。

## 見つけたことは文書に残す

移植で参照した仕様は [docs/vba-analysis.md](../../../docs/vba-analysis.md) に追記する。
毎回抽出し直すのは非効率なので、確認した内容は文書化しておく。
