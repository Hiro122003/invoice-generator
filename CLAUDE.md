# 請求書生成システム

建設現場向け機材レンタル業の月次請求業務を、Excelマクロ（VBA 約6,100行）から
Webアプリへ移行するプロジェクト。**実務投入前提。**

利用頻度は**月1回**。1か月分の請求書・請求明細書を発行して先方へ共有する。

## ドキュメント

| 文書 | 内容 |
|---|---|
| [docs/design.md](docs/design.md) | 要件定義・基本設計書。**まずここを読む** |
| [docs/domain-model.md](docs/domain-model.md) | ドメインモデル。49列の行き先 |
| [docs/vba-analysis.md](docs/vba-analysis.md) | 移行元VBAの実装分析。移植時の参照元 |

## 技術スタック

```
Next.js (App Router / TypeScript)   web/
   │ HTTP / JSON
FastAPI + SQLAlchemy 2.x + Alembic  api/
   │ SQL
PostgreSQL 17 (Docker)              DB名: invoice
```

Excel取込は openpyxl、PDF生成は Playwright（HTML → PDF）。

---

## 破ってはいけないルール

移植の正確性に直結する。**実装前に必ず確認すること。**

### 金額に浮動小数点を使わない

```
DB    NUMERIC
API   Decimal          ← float は禁止
JSON  数値そのまま（文字列化しない）
```

### 消費税は切り上げ（CEIL）。しかも2回、独立して行う

```
請求明細書:  消費税 = CEIL(明細書の税抜 × 税率)
請求書    :  消費税 = CEIL(請求書の税抜 × 税率)
                      ↑ 明細書の消費税を合計するのではない
```

両者は数円ずれることがあるが、**それが現行の正しい挙動**。四捨五入ではない。

### `billing_line.amount` は生成列。アプリから書き込まない

```sql
amount = COALESCE(base_charge,0) * quantity
       + COALESCE(unit_price,0) * COALESCE(duration,1) * quantity
```

VBAの `=(G*F)+(H*I*F)` / `=(G*F)+(H*F)` を1本に統合したもの。
`COALESCE(duration, 1)` が2つの式の差を吸収している。

### 品番の判定文字列は全角

```
ＳＲ－ＷＰＥＴ   ハイフンは全角 U+FF0D。半角 '-' では一致しない
ＰＣＡ           U+FF30 FF23 FF21
```

ハードコードせず `item.tax_category` / `item.billing_group` に持たせる。
取込時に1度だけ判定する。

### 洗い替えは「対象請求期間の4テーブルだけ」

```
洗い替える   billing_line / rental_order / invoice / invoice_statement
             かつ WHERE period_id = 対象月

触らない     他の請求期間（過去月は残す）
             マスタ全般（contract.skip_statement が消えると運用が壊れる）
             issued_document / line_edit_log / period_unlock_log
```

### 値引行は削除せず `is_billable = false` にする

品名に「値引」を含む行は請求対象外（業務ルールとして確定済み）。
ただし監査可能性のためデータは保持する。集計時に `WHERE is_billable` で除外する。

### 元データに自然キーはない

識別番号は 851/937 しかユニークでない。差分更新は原理的に不可能。
**サロゲートキー必須。再取込は洗い替えのみ。**

---

## コマンド

前提: **Docker Desktop を先に起動しておく**（これだけは手動）。
以降は3層とも docker compose が面倒を見るので、`npm run dev` や
`uvicorn` を手で叩く必要はない（`docker-compose.yml` の `command:` に書いてある）。

```bash
# 起動（3層すべて）。--reload / dev サーバー込みで立ち上がる
docker compose up -d

# 状態を見る
docker compose ps

# 止める（DBのデータはボリュームに残る）
docker compose down

# DBに接続
docker compose exec db psql -U postgres -d invoice

# マイグレーション
docker compose exec api alembic upgrade head
docker compose exec api alembic revision --autogenerate -m "説明"

# テスト
docker compose exec api pytest
docker compose exec api pytest -k acceptance    # 受け入れ基準の検証

# ログ
docker compose logs -f api
```

---

## 規約

### 命名

| 対象 | 規則 | 例 |
|---|---|---|
| テーブル・列 | snake_case・単数形 | `billing_line`, `unit_price` |
| Python | snake_case | `calculate_statement_total()` |
| TypeScript | camelCase / コンポーネントは PascalCase | `statementTotal`, `StatementTable` |
| APIパス | 複数形・kebab-case | `/api/periods/{id}/contracts` |

ドメイン用語は日本語の業務語に対応させる。勝手に言い換えない。

| 業務語 | コード |
|---|---|
| 販売先（請求書の宛先） | `customer` |
| 得意先（納品先の会社） | `client` |
| 納品先・現場 | `site` |
| 請求書 | `invoice` |
| 請求明細書 | `invoice_statement` |
| 明細行 | `billing_line` |
| 明細不要 | `skip_statement` |

### コード

- 集計は生SQLを使ってよい。ORMで無理に表現しない
- 金額を扱う関数には必ずテストを書く
- エラーは握りつぶさない。VBAの `Debug.Print` だけという失敗を繰り返さない
- 論理削除は `deleted_at`。物理削除は洗い替え時のみ

---

## 参照ファイル

**リポジトリには含まれない。** 各自のローカル `fixtures/` に置く。

| ファイル | 内容 |
|---|---|
| `fixtures/請求書_ver8(修正版).xlsm` | 移行元のマクロブック |
| `fixtures/202603.xlsx` | 取込用の参照データ 937行 × 49列。4分岐すべてを通すテストデータ入り |
| `fixtures/202603_backup_original.xlsx` | テストデータ追加前の元データ 917行 |

VBAソースを読むときは `/vba-reference` スキルを使う（.xlsm から直接は読めない）。

ファイルが手元にない場合、受け入れ基準の検証（`/acceptance-check`）と
Excel取込のテストは実行できない。ロジックの実装自体は
[docs/vba-analysis.md](docs/vba-analysis.md) の記載で進められる。

### Excel・CSV・PDF はコミットしない

`.gitignore` で `*.xlsx` `*.xlsm` `*.xls` `*.csv` `*.pdf` と `fixtures/` を
**例外なく除外**している。

匿名化したつもりでも、セル以外に情報が残ることがある。実際に
`xl/workbook.xml` の `absPath`（保存元フォルダの絶対パス）に取引先名が
残っていた事例がある。セル・図形・共有文字列を確認しても気づけない。

- `git add -f` で Excel を強制追加しない
- 例外を許可する `!` 行を `.gitignore` に足さない
- 生成したPDF（`storage/`）は実データを含むため常に無視

---

## Git 運用

リモート: `https://github.com/Hiro122003/invoice-generator.git`

### 原則

- **main へ直接コミットしない。** 必ずブランチを切る
- 1コミット = 1つのまとまった変更。**リポジトリが壊れていない状態で区切る**
  （ファイル保存ごとではない）
- コミットしたらプッシュする。作業のバックアップになる
- main への統合は Pull Request 経由。squash merge で main の履歴を保つ

### ブランチ名

```
feat/  新機能        feat/f01-excel-import
fix/   バグ修正      fix/tax-rounding
docs/  文書のみ      docs/design-spec
chore/ 環境・設定    chore/docker-compose
```

フェーズ単位ではなく**機能単位**で切る（フェーズ4は大きすぎる）。

### コミットメッセージ

Conventional Commits の型 + 日本語の説明。

```
feat: Excel取込のバリデーションを実装

49列の構成チェック、請求期間・販売先の単一性チェックを追加。
未知の品番は警告として返し、投入自体は可能にした。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

型は `feat` / `fix` / `docs` / `chore` / `refactor` / `test`。

### 手順

```bash
git switch -c feat/f01-excel-import      # ブランチを切る
# ...作業...
git add -A && git commit -m "feat: ..."  # まとまりで区切る
git push -u origin feat/f01-excel-import

# main へ統合（PRはブラウザ、または gh CLI を導入すればコマンドで）
```

### コミット前に必ず確認

```bash
git status              # 意図しないファイルが乗っていないか
git diff --cached       # 特に Excel・PDF・.env が入っていないか
```

**金額計算に触れた変更は、コミット前に `money-audit` エージェントで監査する。**

---

## 現在地

**フェーズ2（取込）に着手する段階。** フェーズ1は完了・main にマージ済み。

| # | フェーズ | 状態 |
|---|---|---|
| 1 | 基盤構築（Docker / DDL / 雛形） | **完了**（PR #1） |
| 2 | 取込（F-01 / F-02） | これから |
| 3 | リスト表（F-03） | — |
| 4 | 生成ロジック（F-04 / F-06） | — |
| 5 | 手修正（F-05 / F-10） | — |
| 6 | PDF・確定（F-08 / F-09 / F-11） | — |
| 7 | チェック機能（F-07） | — |

フェーズ4完了時は `/acceptance-check` で受け入れ基準を検証する。

### フェーズ1で出来ていること

- `docker compose up -d` で3層が起動する（Next.js 16 / FastAPI / PostgreSQL 17）
- 全17テーブルが Alembic マイグレーション `0001_initial_schema` で作られる
- `billing_line.amount` が生成列として機能し、VBAの計算式を再現している
  （実データの値で検証するテストが `api/tests/test_amount_calculation.py` に11件）
- `GET /api/health/db` で接続とテーブル数を確認できる

### フェーズ2で作るもの

| | |
|---|---|
| F-01 | Excel取込。937行 × 49列を洗い替え投入。マスタは自動UPSERT |
| F-02 | 取込検証。列構成・請求期間の単一性・販売先の単一性・未知の品番 |
| P-01 | 請求期間一覧 `/periods` |
| P-02 | 取込画面 `/periods/[id]/import` |

完了条件は `fixtures/202603.xlsx` を投入して **937行がDBに入る**こと。

このフェーズで初めて VBA の判定ロジックを移植する。

- 品番の全角判定（`ＳＲ－ＷＰＥＴ` / `ＰＣＡ`）→ `item.tax_category` / `billing_group`
- 品名の「値引」判定 → `item.is_billable`
- 会社名の名寄せ（全角スペース・（株）・㈱・（仮称）の除去）→ `client`
- 単価3系統の判別 → `unit_price` + `unit_price_type`
