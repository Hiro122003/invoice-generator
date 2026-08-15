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
DB     NUMERIC
API    Decimal            ← float は禁止
JSON   文字列 "2618740.00" ← Pydantic の既定。精度が一切落ちない
フロント 表示専用          ← 金額の足し算・掛け算をしない
```

JSONで数値にすると JavaScript の float64 を経由してしまう。
文字列で渡し、**合計や税額の計算は必ずサーバー側で行う**。
フロントは `formatYen()` で桁区切りするだけにする。

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

### 49列すべてを実装しているか、設計文書と照合する

`docs/domain-model.md` 5章「元データ49列の行き先」に全列の対応表がある。
過去に**列48「削除」を実装で読み忘れ**、基幹側で削除された明細が
通常行として請求対象に混入する欠陥があった（`fixtures/202603.xlsx` は
この列が全行空のため、受け入れテストでは検出できなかった）。

新しい列を扱うときは、まずこの表で「採用／導出／破棄」のどれかを確認する。
「採用」なのにコードに対応するフィールドが無ければ実装漏れ。
`money-audit` はこの表と実装の突き合わせも行う。

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

**フェーズ6（PDF・確定）の主要部分が完了。** フェーズ1〜5は完了・main にマージ済み。

| # | フェーズ | 状態 |
|---|---|---|
| 1 | 基盤構築（Docker / DDL / 雛形） | **完了**（PR #1） |
| 2 | 取込（F-01 / F-02） | **完了**（PR #3） |
| 3 | リスト表（F-03） | **完了**（PR #4） |
| 4 | 生成ロジック（F-04 / F-06） | **完了**（PR #5） |
| 5 | 手修正（F-05 / F-10） | **完了**（PR #6） |
| 6 | PDF・確定（F-08 / F-09 / F-11） | F-08・F-09は完了。F-11は既存画面（P-04/05/06が任意の期間で動く・確定済みは自動で読み取り専用）とP-10（発行済みPDF再取得）の組み合わせで実質達成。専用の新規画面は作っていない |
| 7 | チェック機能（F-07） | これから |

フェーズ4完了時に `/acceptance-check` の受け入れ基準を検証ずみ（全項目一致）。

### フェーズ1で出来ていること

- `docker compose up -d` で3層が起動する（Next.js 16 / FastAPI / PostgreSQL 17）
- 全17テーブルが Alembic マイグレーション `0001_initial_schema` で作られる
- `billing_line.amount` が生成列として機能し、VBAの計算式を再現している
  （実データの値で検証するテストが `api/tests/test_amount_calculation.py` に11件）
- `GET /api/health/db` で接続とテーブル数を確認できる

### フェーズ2で出来ていること

- `POST /api/imports/validate` → `POST /api/imports` の2段階取込。
  検証はDBを変更せず、投入は対象請求期間の4テーブルだけ洗い替える
- VBAの判定ロジックを `api/app/domain/rules.py` に集約して移植ずみ
  （品番の全角判定・会社名の名寄せ・単価3系統の判別・値引の除外）
- 受け入れ基準は全項目一致。10%税抜 2,583,740円 / 8%税抜 35,000円 /
  契約81 / 得意先5（名寄せ後） / 値引10行 −74,977円
- 洗い替えの境界を実データで検証ずみ（二重計上なし・`skip_statement` 保持・
  他月無傷・確定済み期間は409で拒否）
- 画面: 請求期間一覧 `/periods`、取込画面 `/periods/import`
- Next.jsの開発サーバーは `next dev --webpack`。Docker越しではTurbopackの
  ポーリングが効かず自動反映されなかったため（本番ビルドには影響なし）
- 金額はAPIから**文字列**で返る（例 `"2618740.00"`）。フロントは
  `formatYen()` で表示するだけで、計算はしない

**教訓（money-auditが発見）**: Excel49列目「削除」列を読み忘れ、
基幹側で削除済みの明細が請求対象に混入する欠陥があった。VBAがこの列を
参照せず、参照データも全行空だったためテストで検出できなかった。
**新しい列を扱うときは `docs/domain-model.md` 5章の対応表と実装を必ず突き合わせる。**

### フェーズ3で出来ていること

- `GET /api/periods/{id}/contracts` — 得意先・納品先・契約番号（部分一致）、
  税区分・請求グループ・明細不要・金額範囲でフィルタ。件数と税抜合計を常時返す
- `PATCH /api/contracts/{id}` — 明細不要フラグの切替
- `GET /api/periods/{id}/contracts/{contract_id}/lines` — 契約のドリルダウン
- `GET /api/periods/{id}/contracts/export` — 同条件でCSV出力
- 集計は生SQL。絞り込みは `billing_line.period_id`（専用の部分インデックス
  `ix_billing_line_period_active` が効く列）を直接見る。フィルタ値は
  すべてバインドパラメータ。CSV出力と一覧は同じ `_fetch_rows` を呼ぶため
  絞り込みロジックの二重管理がない
- 画面: リスト表 `/periods/[id]/contracts`

**教訓（ハイドレーション不整合）**: クライアントコンポーネントのrender内で
`<a href>` のURLを組み立てると、サーバー側描画（コンテナ内部URL）と
ブラウザ側描画（公開URL）で値が食い違う。**ブラウザが直接開くURLは
実行環境によらず `PUBLIC_API_BASE` を使う**（`fetch()` 用の `API_BASE` とは別）。
`web/lib/api.ts` を参照。

### フェーズ4で出来ていること

- `POST /api/periods/{id}/generate` — 明細書・請求書を生成（洗い替え）。
  請求書は `(customer_id, tax_category)` ごとに1通、明細書は
  `(invoice_id, contract_id, billing_group)` ごとに1枚。VBAの分岐①②③は
  この GROUP BY に置き換わって消滅した
- `GET /api/periods/{id}/invoices` / `GET /api/invoices/{id}` — 請求書
- `GET /api/invoices/{id}/statements` / `GET /api/statements/{id}` — 明細書
- 金額は確定するまで保存せず、常時集計クエリで算出。消費税は明細書ごと・
  請求書ごとに2回独立してCEIL（`api/app/api/statements.py` の
  `_INVOICE_LIST_SQL` / `_STATEMENT_LIST_SQL`）
- 受け入れ基準は全項目一致。請求書2通・明細書87枚（10%側83+8%側4）、
  10%消費税258,374円・8%消費税2,800円、分岐①76/②3/③1
- 再生成は洗い替え。対象請求期間の `invoice`/`invoice_statement` だけを
  作り直す。`billing_line.statement_id` は `ON DELETE SET NULL` で
  自動的に外れる（明細行本体は触らない）

**教訓（money-auditが発見）**: `GET /api/invoices/{id}/statements` が
`s.contract_id` をSELECTし忘れ、`_statement_row_to_out` の
`r.get("contract_id", 0)` がエラーを握りつぶして常に0を返していた。
金額には影響しなかったが、**フォールバック付きの `.get()` でSQLの
列漏れを握りつぶさない**。`r["contract_id"]` のように直接参照すれば
列が無いとき即座に落ちて気づける。

### フェーズ5で出来ていること

- `PATCH /api/lines/{id}` — 明細手修正。数量・基本料・単価・日数/月数の
  4項目のみ受け付ける。金額（`amount`）は生成列のままで直接は書き込め
  ない。1リクエストで明細行・明細書・請求書の3階層の金額をまとめて返す
- `POST /api/lines/{id}/reset` — 取込時の値（`src_*`）に戻す
- `GET /api/lines/{id}/history` — 修正履歴（誰が・いつ・何を・いくらから
  いくらに）。`line_edit_log` は同値へのPATCHでは記録しない
- `GET /api/periods/{id}/statements` — 請求期間内の全明細書を横断する
  一覧（会社・税率・請求グループでフィルタ、修正済みにマーク）
- `compute_statement_totals` / `compute_invoice_totals`
  （`api/app/api/statements.py`）にCEIL計算を集約。LIST用SQLと手修正の
  応答とで計算式がズレない
- 確定済み期間（`PeriodStatus.CONFIRMED`）への書き込みは全経路で拒否
  （409）。実HTTPで検証ずみ
- 画面: 請求書 `/periods/[id]/invoices`（**生成ボタンはここ**）、
  明細書一覧 `/periods/[id]/statements`、
  明細書詳細・編集 `/statements/[id]`（セルをクリックしてその場編集）
- ブラウザで実際に編集操作を検証ずみ。数量3→5への変更が
  明細行→明細書→請求書の3階層に即座に連動し、「戻す」で完全復元

**教訓（money-auditが発見）**: `reset_line` に `update_line` と同じ
「数量をnullにできない」防御がなかった。現状は到達不能な経路（取込時に
必ず数値が入るため）だが、将来「行を追加する」機能ができたときに
壊れないよう、念のため揃えた。

### フェーズ5マージ後に見つかった欠陥（`fix/skip-statement-generation`）

`docs/design.md` の想定運用は「P-03で明細不要を設定 → 生成」の順だが、
フェーズ4の `generator.py` が `contract.skip_statement` を一度も
参照しておらず、**明細不要にチェックを入れても普通に請求されていた**。
`generate()` の生成対象SQLに `AND NOT c.skip_statement` を追加して修正。
`invoice_statement`/`invoice` はこの絞り込み結果からしか作られないため、
直す箇所はここ1箇所で足りる（`_INVOICE_LIST_SQL` 等は
`invoice_statement` を起点に集計するので追随して直る）。

発見した経緯も記録: 修正後に受け入れ基準テストが4件失敗した。原因は
バグではなく、フェーズ3のP-03画面を手動検証した際に契約24へ
「明細不要」を付けたまま戻し忘れていたこと（`skip_statement`は洗い替え
対象外なので残り続ける）。これまでフラグが無視されていたため気づかず
放置されていた。**手動検証でmasterテーブルの値をトグルしたら、検証後に
元へ戻す。** `skip_statement`のような「洗い替えで消えない」フラグは
特に忘れやすい。

### フェーズ6: P-03↔P-06連携（発行前プレビュー）

P-03（リスト表）とP-06（請求書）の間に画面遷移がなく、「明細不要にした
契約が本当に生成から除外されるか」を確認する手段がなかった。VBA原本には
「フィルタ」自体が存在せず（`リスト表`はただの一覧シート）、生成マクロが
見ているのはE列の明細不要チェックだけ。得意先・契約番号などの検索は
Webアプリで追加した表示専用の絞り込みで、生成には影響しない
（`docs/vba-analysis.md` 2章）。この前提のうえで:

- P-06に発行前プレビューを追加。`GET /api/periods/{id}/contracts?skip_statement=true`
  を呼ぶだけ（新規エンドポイントなし）で、現時点で除外される契約の件数・
  税抜合計・内訳を生成前に確認できる
- P-03↔P-06に相互のナビゲーションリンクを追加
- プレビューの「リスト表で調整する」は `?skip_statement=true` 付きで
  P-03へ遷移し、明細不要のみに絞り込んだ状態で開く

**ヒヤリハット**: 動作確認中、DBを見たら契約23〜46番の24件が明細不要に
なっており、自分の操作（1件のみ）と食い違っていた。原因はユーザーが
並行して同じ画面でトグルを試していたため。**共有の開発DBを触る画面は、
自分以外も同時に操作している可能性がある。** 想定外の状態を見つけたら
即座に作業を止めて確認し、黙って上書き・巻き戻ししない。

### フェーズ6: P-06を開くたびに自動生成する（「古さ」検知は廃止）

ユーザー報告: 契約7000000000001を明細不要にしたのに請求書には残ったまま
で、代わりに無関係な契約2〜5が消えていた。調査したところ**計算バグでは
なかった**。`invoice`/`invoice_statement`は「生成する」を押した瞬間の
スナップショットで、フラグを変えただけでは自動更新されない
（洗い替えは明示的に再生成したときだけ走る設計）。このとき表示されて
いたのは、契約2〜5がまだ明細不要だった**過去の生成結果**で、その後
フラグを2〜5→OFF、1→ONへ変えたが再生成していなかった。再生成すると
税抜2,566,764円（=2,583,740−16,976、契約1が正しく除外）に一致した。

最初は「古い生成結果を検知して警告する」方式で対応したが、ユーザーから
「ややこしい」とフィードバックがあり、根本から作り直した。**P-06を
開くたびに、確定済み期間でない限り自動で`generate()`を呼び直す**
（`web/app/periods/[id]/invoices/page.tsx`）。これによりP-03の明細不要
設定が常にそのまま反映された状態で開き、警告表示自体が不要になった。
確定済み（409）・請求対象なし（422）は想定内としてエラー表示しない。
あわせて `GET /api/periods/{id}/invoices` に全体合計（8%・10%をまたいだ
税抜/消費税/合計）を追加した。ユーザーがP-03の合計とP-06の個々の請求書
金額を頭の中で突き合わせて「計算が合わない」と誤解した実例があったため
（比較先が「両税率合算の合計」なのか「片方の請求書だけの合計」なのか
画面から分からなかった）。合計はバックエンドでDecimalのまま`sum()`する
だけで、新たにCEILし直さない（フロントは`formatYen()`で表示するだけ、
CLAUDE.md冒頭のルール）。

**money-auditが発見した重大な副作用**: 「開くたびに自動生成」は
Reactの副作用として素朴に実装すると、React StrictMode（開発モードで
effectをmount→cleanup→mountと二重発火させる仕様）により`generate()`が
同一period_idに対してほぼ確実に2回並列で呼ばれる。`generate()`は
洗い替え（一度に1本しかinvoiceを書けない）なので、片方が
`invoice`の一意制約（period_id, customer_id, tax_category）に
ぶつかって500になる実害を確認した（同時2〜8並列のcurlで再現）。
しかも失敗時のエラー表示は成功後も自動で消えず、「金額は正しいのに
赤いエラーが出続ける」矛盾した画面になっていた。

対策は2段构え:
- バックエンド: `generator.generate()` の先頭で
  `pg_advisory_xact_lock(period_id)` を取得し、同一period_idへの
  同時生成をDBレベルで直列化する（コミット/ロールバックで自動解放）。
  実際に16並列でPOSTしても全件200になることを確認ずみ
- フロント: 自動生成のuseEffectを`AbortController`対応にし、
  StrictModeの1本目（cleanupで即abortされる）が`AbortError`を
  無視するようにした。これで実際に完走するのは1本だけになる
- **アドバイザリロックが本質的な修正で、AbortControllerは重複リクエスト
  を減らす補助**。どちらか片方が漏れても、もう片方が実害（500・二重
  計上）を防ぐ設計にした

**教訓**: 「画面を開いたら副作用としてAPIを叩く」実装は、React
StrictModeの二重発火を必ず疑う。フロント側のガード（cancelledフラグ・
AbortController）だけに頼らず、**サーバー側でも同時実行を安全に
さばけるようにする**（アドバイザリロックやON CONFLICT等）。

### フェーズ6: F-08 確定・締め で出来ていること

- `POST /api/periods/{id}/confirm` — 期間内の全請求書・明細書について
  既存の `compute_invoice_totals`/`compute_statement_totals`（F-05で
  作った集計）をそのまま呼び、結果を `total_ex_tax`/`tax_amount`/
  `total_amount` へスナップショットとして書き込む。新しい税額計算式は
  増やしていない。`billing_period.status` を `CONFIRMED` にする
- `POST /api/periods/{id}/unconfirm` — `reason` 必須。スナップショットを
  全部NULLに戻し「常に集計クエリで算出」の状態へ戻す。
  `period_unlock_log` に記録
- 版数（`invoice.revision`）: 初回確定は1のまま、`invoice.confirmed_at`
  が非NULL（＝過去に確定歴あり）なら再確定のたびに+1。確定解除では
  `confirmed_at` をクリアしない（次回確定時の判定・履歴表示に使う）
- 画面: P-06に確定・確定解除UI（`ConfirmBar`）。確定済みなら自動生成
  （generate()）自体をスキップし、「生成し直す」ボタンと明細不要
  プレビューも隠す

**教訓（money-auditが発見・重大）**: `confirm_period`/`unconfirm_period`
と `PATCH /api/lines/{id}`（手修正）が、どちらも「`period.status`を
読む→判断→書く」という check-then-act で、互いを排他する仕組みが
なかった。`generator.generate()` は同種の事故（invoiceの一意制約違反）
を理由に既に `pg_advisory_xact_lock(period_id)` を入れていたのに、
確定処理と手修正には入っていなかった。money-auditが2セッションを
意図的にインターリーブさせて実際に再現: 手修正が「確定済みでない」を
確認した直後に別セッションで確定が完走し、その後手修正がそのまま
書き込みに成功した。**「確定済み期間は編集不可」の前提が、タイミング
次第で崩れていた**（画面のGET系APIは元々スナップショット列を読まず
常にライブ集計する設計のため、この不整合がそのまま画面に出てしまう）。

`confirm_period`/`unconfirm_period`（`api/app/services/confirmation.py`）、
`_check_not_locked`（`api/app/api/lines.py`）、`run_import`
（`api/app/services/importer.py`）の4箇所すべてに、状態を読む前に
同じ `pg_advisory_xact_lock(period_id)` を追加。これで
generate/import/PATCH/confirm/unconfirmが同一期間について自然に
直列化される。修正後、PATCHと確定を10回連続で意図的に競合させ、
最終的な確定スナップショットが最後のPATCH後の値と厳密に一致することを
実データで確認ずみ（税抜2,583,740円 + 数量差分109,461円 = 2,693,201円、
1円のずれもなし）。

**教訓**: 「状態を確認してから書き込む」処理を追加するときは、既存の
類似処理（このプロジェクトでは `generator.generate()`）が
同時実行対策を持っていないか必ず確認し、同じロックキーで揃える。
片方だけ対策しても、対策していない方から抜けられる。

### フェーズ6: F-09 PDF出力 で出来ていること

- Playwright（headless Chromium）でHTML→PDF。VBAの「44行オフセット」
  計算（`docs/vba-analysis.md` 6章の問題#1）は完全に消滅し、
  `page-break-inside`相当のページ単位のHTMLセクションに置き換わった。
  APIコンテナに`playwright install --with-deps chromium`が必要
  （`api/Dockerfile`）。日本語フォントはこの依存インストールで
  IPAGothicが一緒に入るため追加設定は不要だった
- `POST /api/periods/{id}/export` — 請求書・請求明細書PDF（税率ごと）と
  それらをまとめたZIPを1回で作成。`storage_dir`（`docker-compose.yml`で
  `./storage:/storage`にマウント済み、フェーズ1から用意されていた）に
  保存し、`issued_document`へ記録（フェーズ1から存在したが未使用だった
  テーブルをここで初めて使う）。ファイル名は
  `2025-03_請求書_10%_rev1.pdf`のように版数を含める
- `GET /api/periods/{id}/documents` / `GET /api/documents/{id}/download`
  — 発行済み一覧・再ダウンロード。削除しない（洗い替え対象外）
- 画面: P-08 `/periods/[id]/export`（出力＋直近の一覧）、
  P-10 `/periods/[id]/documents`（全件一覧・再ダウンロード）
- 金額はPDF側で一切計算しない。既存の`compute_invoice_totals`/
  `_STATEMENT_LIST_SQL`（F-05で作った集計）をそのまま埋め込むだけ

**教訓（money-auditが発見・重大）**: `export_period()`にも
`pg_advisory_xact_lock`が抜けていた。確定・PATCH・generate・importには
既に入れていたのに、export だけ対策漏れ。未確定期間はexport中もPATCHで
の手修正が同時に走りうる設計（design.mdの「試し刷り」は未確定期間でも
出力可）ため、1回の出力の中で「請求書ヘッダの合計」「明細書サマリ行」
「明細書自身のページ」がそれぞれ別のSQL文（＝別スナップショット）を
見てしまい、同一PDF内で数字が食い違う経路があった。同じロックキーを
追加して直列化。**「状態を確認してから書き込む/読む」処理を追加したら、
既存の類似処理全部に同じ対策が入っているか横並びで確認する**
（このプロジェクトで3回目の同種の指摘）。

**教訓（money-auditが発見）**: PDF側の金額整形（`_yen()`）が
Pythonの既定の丸め（`f"{d:,.0f}"` は ROUND_HALF_EVEN、銀行丸め）に
委ねてしまい、画面側（`web/lib/api.ts`のformatYen、JSのtoLocaleStringは
半分を切り上げ側へ丸める）と丸め方向がずれるケースを実測で発見
（1234.50円がPDF側"1,234"・画面側"1,235"）。通常のExcel由来データは
円未満の端数を持たないため気づきにくいが、F-05の手修正で単価に小数を
入れると実際にリーチ可能。`_yen()`をROUND_HALF_UPで明示的に丸め、
画面側と揃えた。**「ここでは計算しない」と書いたコードでも、表示用の
桁丸めは実質的な計算であり、既定値に丸め方式を委ねない。**
