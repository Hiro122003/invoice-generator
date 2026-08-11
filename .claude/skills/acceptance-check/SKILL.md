---
name: acceptance-check
description: 移植したロジックが現行VBAと同じ結果を出すか検証する。請求書・明細書の生成ロジックを実装または変更したあと、契約数・税抜合計・分岐カバレッジが期待値と一致するかを確認したいときに使う。フェーズ4の完了判定にも使う。
---

# 受け入れ基準の検証

現行VBAが `fixtures/202603.xlsx` を処理した結果と、アプリの出力が一致するかを確認する。
**金額が1円でもずれたら移植は失敗。**

## 期待値

`fixtures/202603.xlsx`（937行 × 49列）に対する現行VBAの出力。

| 検証項目 | 期待値 |
|---|---|
| 取込行数 | 937 |
| 契約数 | 81（うちカウンタのみ 1件） |
| 請求先会社数（名寄せ後） | 5 |
| 10% 税抜合計 | 2,583,740 円 |
| 8% 税抜合計 | 35,000 円 |
| 請求対象外（値引） | 10 行 / −74,977 円 |
| 分岐① 備品のみ | 76 組 |
| 分岐② 備品＋カウンタ | 3 組 |
| 分岐③ カウンタのみ | 1 組 |
| 明細書ページ数 | 10%側 84ページ＋カウンタ分 / 8%側 4枚 |

会社ごとの内訳:

| 得意先 | 行数 |
|---|---|
| サンプル工業株式会社 | 239 |
| 架空建設株式会社 | 212 |
| テスト開発株式会社 | 192 |
| 仮想設備株式会社 | 155 |
| デモ総合建設株式会社 | 119 |

## 1. 元データ側の期待値を再計算する

アプリの実装に依存せず、**参照データから直接**期待値を出す。
アプリ側にバグがあっても、こちらは正しい値を返す。

```bash
cd "C:/Users/hiroy/Desktop/invoice-generator" && python -W ignore -c "
import pandas as pd, io, math
out = io.StringIO(); P = lambda *a: print(*a, file=out)
WPET = ''.join(chr(c) for c in (0xFF33,0xFF32,0xFF0D,0xFF37,0xFF30,0xFF25,0xFF34))
PCA  = ''.join(chr(c) for c in (0xFF30,0xFF23,0xFF21))

df = pd.read_excel('fixtures/202603.xlsx', sheet_name='匿名化データ')
df['_wpet'] = df['品番'] == WPET
df['_pca']  = df['品番'].astype(str).str.contains(PCA, na=False)
df['_neg']  = df['品名'].astype(str).str.contains('値引', na=False)

P(f'取込行数      {len(df)}')
P(f'契約数        {df[\"契約番号\"].nunique()}')
P(f'10% 税抜合計  {df[(~df[\"_wpet\"]) & (~df[\"_neg\"])][\"小計\"].sum():,.0f}')
P(f'8%  税抜合計  {df[( df[\"_wpet\"]) & (~df[\"_neg\"])][\"小計\"].sum():,.0f}')
P(f'値引          {df[\"_neg\"].sum()} 行 / {df[df[\"_neg\"]][\"小計\"].sum():,.0f} 円')

b1 = b2 = b3 = 0
for _, s in df.groupby(['契約番号','納品先名称']):
    eq = ((~s['_wpet']) & (~s['_pca']) & (~s['_neg'])).sum()
    ct = ((~s['_wpet']) & ( s['_pca']) & (~s['_neg'])).sum()
    if   eq >  0 and ct == 0: b1 += 1
    elif eq >  0 and ct >  0: b2 += 1
    elif eq == 0 and ct >  0: b3 += 1
P(f'分岐 ①{b1} ②{b2} ③{b3}')
open('<SCRATCHPAD>/expected.txt','w',encoding='utf-8').write(out.getvalue())
print('ok')
"
```

出力は必ずファイルへ。標準出力だと日本語が化ける。

## 2. アプリ側の実測値を取る

```bash
docker compose exec db psql -U postgres -d invoice -A -F' | ' -c "
SELECT
  (SELECT count(*) FROM billing_line WHERE period_id = 1 AND deleted_at IS NULL) AS 行数,
  (SELECT count(DISTINCT contract_id) FROM billing_line bl
     JOIN rental_order o ON o.id = bl.order_id WHERE bl.period_id = 1) AS 契約数,
  (SELECT count(*) FROM billing_line WHERE period_id = 1 AND NOT is_billable) AS 値引行数;
"
```

税率ごとの税抜合計:

```bash
docker compose exec db psql -U postgres -d invoice -A -F' | ' -c "
SELECT i.tax_category, SUM(bl.amount) AS 税抜
FROM billing_line bl
JOIN item i ON i.id = bl.item_id
WHERE bl.period_id = 1 AND bl.is_billable AND bl.deleted_at IS NULL
GROUP BY i.tax_category ORDER BY 1;
"
```

分岐のカバレッジ:

```bash
docker compose exec db psql -U postgres -d invoice -A -F' | ' -c "
SELECT billing_group, count(*) AS 明細書数
FROM invoice_statement s JOIN invoice v ON v.id = s.invoice_id
WHERE v.period_id = 1 GROUP BY 1 ORDER BY 1;
"
```

## 3. 突き合わせる

期待値と実測値を並べて比較する。**ずれたら原因を特定するまで先へ進まない。**

### ずれたときに疑う順序

| 症状 | 疑う箇所 |
|---|---|
| 8% が 0 円 | 品番の判定が半角になっている（`ＳＲ－ＷＰＥＴ` のハイフンは U+FF0D） |
| 10% が 74,977 円多い | 値引行を除外していない（`WHERE is_billable`） |
| 10% が 163,500 円少ない | カウンタ行を10%側から落としている（カウンタは**10%に含む**） |
| 分岐③ が 0 | カウンタのみの契約（7000000000081）を拾えていない |
| 契約数が 80 | 同上。新規契約の取込漏れ |
| 端数が数円ずれる | 四捨五入している。**切り上げ（CEIL）が正** |
| 金額が小数でずれる | float を使っている。`Decimal` / `NUMERIC` に統一する |

### 消費税の検算

切り上げが**2回、独立して**行われる点に注意。

```
明細書ごと:  CEIL(明細書の税抜 × 税率)
請求書    :  CEIL(請求書の税抜 × 税率)   ← 明細書の消費税を合計するのではない
```

Σ（明細書の消費税） ≠ 請求書の消費税 になるのは**正常**。
一致してしまっていたら、むしろ実装が間違っている。

## 4. pytest に落とす

一度手で確認したら、テストとして固定する。

```python
# api/tests/test_acceptance.py
EXPECTED = {
    "rows": 937,
    "contracts": 81,
    "total_10": Decimal("2583740"),
    "total_8":  Decimal("35000"),
    "discount_rows": 10,
    "discount_total": Decimal("-74977"),
    "branch": {"equipment_only": 76, "both": 3, "counter_only": 1},
}
```

```bash
docker compose exec api pytest -k acceptance
```

## 参照

- 期待値の根拠: [docs/vba-analysis.md](../../../docs/vba-analysis.md) 7章
- 計算仕様: [docs/design.md](../../../docs/design.md) 5章
- VBAの原典を確認する場合: `/vba-reference` スキル
