# 請求書生成システム 要件定義・基本設計書

Excelマクロ（VBA 約6,100行）で運用している月次請求業務を、Webアプリケーションへ移行する。
**実務投入を前提**とする。

| | |
|---|---|
| 移行元 | `fixtures/請求書_ver8(修正版).xlsm`（26モジュール / 約6,100行 / 8シート） |
| 参照データ | `fixtures/202603.xlsx`（937行 × 49列） |
| 技術スタック | Next.js + FastAPI + PostgreSQL 17（Docker） |
| 利用頻度 | **月1回**。1か月分の請求書・明細書を発行して先方へ共有 |

関連文書: [ドメインモデル](./domain-model.md) / [VBA解析](./vba-analysis.md)

---

## 1. スコープ

### やること

- 基幹システムから出力したExcelを取り込み、請求書・請求明細書を生成する
- 生成後の明細を**ブラウザ上で手修正**し、金額を自動再計算する
- 契約データを**得意先・納品先・契約番号で絞り込んで閲覧**する
- 請求書・請求明細書をPDFで出力する
- 発行前の妥当性チェック（請求漏れ・異常値の検出）
- 締め処理、修正履歴の記録、発行済みPDFの永続保管

### やらないこと

- 基幹システムとの直接連携（Excelを介した受け渡しのまま）
- 入金消込・売掛管理
- メール送付の自動化（PDF出力まで）
- 端数値引・得意先別値引率（VBAでコメントアウトされている要件。**列だけ用意し機能は後日**）

### 確定している業務ルール

| ルール | 内容 |
|---|---|
| 請求先 | 販売先1社あて1通。裏付けとして現場ごとの請求明細書を添付 |
| 税率 | 品番 `ＳＲ－ＷＰＥＴ`（飲料水）のみ8%、それ以外10%。相互排他 |
| 明細書の分割 | 品番に `ＰＣＡ` を含むもの（カウンタ類）は同一契約でも別の明細書 |
| 請求対象外 | 品名に「値引」を含む行は請求に含めない（データとしては保持） |
| 端数処理 | **現行踏襲**。明細書ごと・請求書ごとに個別に切り上げ（→ 5章） |
| 取込単位 | 1ファイル＝1販売先 × 1請求期間（基幹側で絞り込み済み） |

---

## 2. 業務フロー

### 現行（Excelマクロ）

```
基幹システムからExcelダウンロード
  → dataシートに貼付
  → 「リスト表作成」ボタン
  → 明細不要に×を手入力（毎月やり直し）
  → 「一括処理」ボタン
  → シート上で数値を直接修正
  → PDF保存
```

### 移行後

```
基幹システムからExcelダウンロード
  → アップロード
  → 検証結果を確認（エラー・警告）
  → リスト表で内容確認（明細不要は前月から継承）
  → 明細書・請求書 生成
  → ブラウザで手修正（金額は自動再計算）
  → 発行前チェック
  → 確定（ロック）
  → PDF出力 → 先方へ共有
```

### 差が出る3点

1. **「明細不要」の設定が翌月へ引き継がれる**（現行は毎月やり直し）
2. **取込時に不正データを検出できる**（現行は無検査）
3. **確定後はロックされ、誤って上書きできない**（現行は無防備）

---

## 3. 機能要件

| ID | 機能 | 内容 | 由来 |
|---|---|---|---|
| F-01 | Excel取込 | xlsxをアップロードし49列を検証、洗い替え投入。マスタは自動UPSERT | dataシートへの手貼付 |
| F-02 | 取込検証 | 列構成・請求期間の単一性・販売先の単一性を検査。未知の品番は警告 | 新規 |
| F-03 | リスト表 | 契約一覧を**得意先・納品先・契約番号でフィルタ**。明細不要の設定もここ | `リスト表作成()` |
| F-04 | 明細書生成 | 契約×請求グループ単位で生成。8%/10%を同一処理で扱う | `請求明細書出力10()/8()` |
| F-05 | 明細手修正 | **数量・基本料・単価・日数/月数を編集**。金額と合計を即時再計算 | 新規 |
| F-06 | 請求書生成 | 明細書の合計を積み上げ。税率ごとに1通 | `請求書10/8_請求明細から抽出()` |
| F-07 | 発行前チェック | 請求漏れ・単価0・期間外・前月比の異常を一覧 | `明細数の確認()` |
| F-08 | 確定・締め | 請求期間を確定してロック。解除は理由を記録し、再確定で版数+1 | 新規 |
| F-09 | PDF出力 | 会社ごと分割とZIP一括。**発行済みPDFは版数ごとに保管** | `CreatePDFOfBill()` |
| F-10 | 修正履歴 | 誰がいつ何をいくらからいくらに変えたか | 新規 |
| F-11 | 過去請求の閲覧 | 過去月の請求書・明細書を参照。発行済みPDFの再取得 | 新規 |

### F-05 明細手修正 — 詳細

| 項目 | 仕様 |
|---|---|
| 編集可能 | **数量 / 基本料 / 単価 / 日数・月数** の4項目 |
| 編集不可 | 納品日・引取日・受注番号・品番・品名（基幹データの同一性を保つ） |
| 金額 | **直接編集させない。** 4項目から常に自動計算 |
| 再計算範囲 | 明細行の金額 → 明細書の税抜・消費税・合計 → 請求書の合計 |
| 可視化 | 修正行にマーク。取込時の値をホバーで確認 |
| 取消 | 行単位・明細書単位で取込値に戻せる |
| 制約 | 請求期間が確定済みなら編集不可 |
| 記録 | 変更前後・実行者・日時を `line_edit_log` に記録 |

> **金額を直接編集させないのは意図的。**
> 「数量×単価と金額が合っていない明細書」を物理的に作れなくするため。
> VBAが金額欄に値ではなく数式 `=(G*F)+(H*I*F)` を残していた設計思想を引き継ぐ。

### F-03 リスト表 — 詳細

| 項目 | 仕様 |
|---|---|
| フィルタ | **得意先 / 納品先名称 / 契約番号**（部分一致・複数選択可） |
| 追加フィルタ | 税区分（10%/8%）・請求グループ（備品/カウンタ）・明細不要・金額範囲 |
| 表示列 | 契約番号・得意先・納品先名称・住所・明細行数・税抜金額・明細不要 |
| ドリルダウン | 行クリックでその契約の明細行一覧へ |
| 操作 | 明細不要フラグのトグル（**翌月以降も継承**） |
| 集計表示 | 絞り込み結果の件数と金額合計を常時表示 |
| 書き出し | 絞り込み結果をCSV出力 |

---

## 4. 状態遷移と排他制御

本システムは**月に1度**、1か月分を発行するために使う。
請求期間ごとに独立した処理であり、月をまたいでデータを引き継ぐことはない。

```
未取込 ──アップロード──▶ 取込済 ──確定──▶ 確定済
                          ▲   │              │
                          └───┘              │
                    再アップロード            │
                （無条件で洗い替え）          │
                          ▲                  │
                          └──確定解除────────┘
                             （理由を記録）
```

| 状態 | 再アップロード | 手修正 | 確定 | PDF出力 |
|---|---|---|---|---|
| **取込済** | 無条件に洗い替え（確認・警告なし） | 可 | 可 | 可（試し刷り） |
| **確定済** | **拒否**（409） | 不可 | — | 可（正式） |

### 手修正はその月かぎり

基幹システムのデータは月ごとに単価や数量が変動するため、前月の手修正を翌月へ引き継ぐ意味がない。
翌月のアップロードには、その月として正しい値が入っている前提に立つ。

同じ月を再アップロードした場合も手修正は破棄され、取込値に戻る。確認ダイアログは挟まない。
**唯一の歯止めは「確定」による凍結。**

### 洗い替えの範囲

```
洗い替える（対象請求期間のみ）
  billing_line / rental_order / invoice / invoice_statement

影響しない
  他の請求期間のデータ            ← 過去月はそのまま残る
  contract / site / client / item / customer / sales_rep / office
  issued_document                 ← 発行済みPDF
  line_edit_log / period_unlock_log / import_batch
```

`contract.skip_statement`（明細不要フラグ）はExcelに存在せず、利用者が画面で設定する情報。
金額のような月次の変動値ではなく**「この現場は明細書を出さない」という継続的な取り決め**なので、
洗い替えの対象外とし翌月以降も引き継ぐ。

### 想定する運用

1. 月初、基幹システムから前月分を取引先コード・請求期間で絞り込んでダウンロード
2. アップロード。検証結果を確認して投入
3. リスト表で内容を確認。明細不要の設定を調整
4. 明細書・請求書を生成
5. 単価や数量に誤りがあればブラウザ上で手修正
6. 発行前チェックを通し、確定してロック
7. PDFを出力し、先方へ共有
8. 翌月、1に戻る（前月のデータは履歴として残る）

### 送付後の訂正フロー

請求書を送付したあとに先方から誤りを指摘される場合がある。

```
確定済（第1版を送付）
  → 確定解除（理由を記録）
  → 明細を手修正
  → 再確定（版数 → 第2版）
  → PDF再発行（訂正版を送付）

第1版PDF・第2版PDF はいずれもサーバーに保管され続ける
```

| 要素 | 仕様 |
|---|---|
| 版数 | `invoice.revision`。確定するたびに +1。第1版は 1 |
| 確定解除 | **理由の入力を必須**とし、実行者・日時とともに記録 |
| 発行済みPDF | **削除せず保管**。版数ごとに残し、いつでも再取得できる |
| ファイル名 | `2026-03_請求書_10%_rev2.pdf` のように版数を含める |
| 差分の確認 | 修正履歴（F-10）でどの明細を何から何に変えたかを追える |

> **「先方に何を送ったか」の正は、DBの数値ではなく発行済みPDF。**
> DBの値は訂正で上書きされるが、PDFは発行時点の姿を固定して保持する。

---

## 5. 金額計算仕様

現行VBAが埋め込んでいるExcel数式をそのまま移植する。端数処理は**現行踏襲**。

### ① 明細行の金額

```
VBA（Excel数式として明細書に埋め込まれる）
  日数/月数あり :  =(G*F)+(H*I*F)
  日数/月数なし :  =(G*F)+(H*F)
      F=数量  G=基本料  H=単価  I=日数/月数  J=金額

アプリ（PostgreSQL の生成列）
  amount = COALESCE(base_charge, 0) * quantity
         + COALESCE(unit_price, 0) * COALESCE(duration, 1) * quantity
```

`COALESCE(duration, 1)` により、日数/月数が空のときは `単価 × 数量` となり、
VBAの2つの式が1本に統合される。
**生成列にすることで「数量×単価と金額が食い違う行」がDB上に存在できなくなる。**

### ② 請求明細書の合計（1枚ごと）

```
税抜   = SUM(amount) WHERE is_billable = true
消費税 = CEIL(税抜 × 税率)      -- VBA: ROUNDUP(J*0.1, 0) 切り上げ
合計   = 税抜 + 消費税
```

### ③ 請求書の合計（税率ごとに1通）

```
税抜   = SUM(明細書の税抜)
消費税 = CEIL(税抜 × 税率)      -- VBA: RoundUp(beforeTax*0.1, 0)
合計   = 税抜 + 消費税
```

> **消費税の切り上げが2回、独立して行われる。**
> 請求書の消費税は「明細書の消費税の合計」ではなく「明細書の税抜の合計」から改めて計算される。
> そのため両者は数円ずれることがあるが、**これが現行の挙動**。
>
> 適格請求書の要件では端数処理は税率区分ごとに1回が原則のため、
> 将来 `tax_rounding_mode` 設定で制度準拠モードを選べる余地を残す。

### ④ 再計算の連鎖

```
利用者が「数量」を 3 → 5 に変更
  → billing_line.amount                    生成列が自動再計算
  → invoice_statement 税抜/消費税/合計      集計クエリで再計算
  → invoice 税抜/消費税/合計                集計クエリで再計算
  → 画面に即時反映（1リクエストで完結）
```

確定前は常に集計クエリで算出し、**確定時に `invoice` / `invoice_statement` へスナップショットを書き込む**。
これにより発行済み請求書の金額が後から動くことがない。

---

## 6. 画面設計

| ID | 画面 | パス | 内容 |
|---|---|---|---|
| P-01 | 請求期間一覧 | `/periods` | 月ごとのカード。状態・件数・請求額・最終更新 |
| P-02 | 取込 | `/periods/import` | xlsxをドロップ → 検証結果 → 承認して投入。<br>投入時に請求期間をExcelの中身から特定・作成するため `[id]` を前提にしない |
| P-03 | **リスト表** | `/periods/[id]/contracts` | 得意先・納品先・契約番号でフィルタ。明細不要のトグル |
| P-04 | 請求明細書 一覧 | `/periods/[id]/statements` | 会社・税率・請求グループで絞り込み。修正済みにマーク |
| P-05 | **明細書 詳細・編集** | `/statements/[id]` | 紙面レイアウトを再現。セルを直接編集し合計が即時更新 |
| P-06 | 請求書 | `/periods/[id]/invoices` | 10%・8%の2通。行クリックで対応する明細書へ |
| P-07 | 発行前チェック | `/periods/[id]/validate` | 請求漏れ・単価0・期間外・前月比異常を重要度つきで |
| P-08 | PDF出力 | `/periods/[id]/export` | 会社ごと分割・ZIP一括。ファイル名を自動命名 |
| P-09 | 修正履歴 | `/periods/[id]/history` | いつ・誰が・何を・いくらから いくらに。確定解除の理由も |
| P-10 | 発行済み書類 | `/periods/[id]/documents` | 過去の発行PDFを版数つきで一覧・再ダウンロード |

### P-05 の編集操作

| 操作 | 挙動 |
|---|---|
| セルをクリック | その場で入力可能（インライン編集） |
| 値を入力して確定 | PATCH送信 → 金額・明細書合計・請求書合計を再計算し画面に反映 |
| Tab / Enter | 次のセルへ移動。**Excelに近い操作感を保つ** |
| Esc | 入力を取り消す |
| 修正済みセル | 背景色とマーカーで識別。ホバーで「元: 3」と表示 |
| 行の取消ボタン | その行を取込時の値に戻す |
| 確定済み期間 | 全セルが読み取り専用。ロックアイコン表示 |

---

## 7. アーキテクチャ

```
ブラウザ
   │ HTTP / JSON
Next.js  (App Router / TypeScript)     画面・入力・表示
   │ HTTP / JSON
FastAPI  (Python)                      Excel取込 / 金額計算 / PDF生成 / DB操作
   │ SQL (SQLAlchemy)
PostgreSQL 17  (Docker)                データベース名: invoice
```

| 層 | 採用 | 役割 |
|---|---|---|
| フロント | Next.js (App Router) + TypeScript | 画面。編集はインライン、状態管理は最小限 |
| API | FastAPI + Pydantic | 入出力の型検証を自動化。OpenAPI仕様が自動生成 |
| ORM | SQLAlchemy 2.x | PythonオブジェクトとSQLの相互変換。複雑な集計は生SQL併用 |
| マイグレーション | Alembic | テーブル定義の変更履歴を管理・適用 |
| DB | PostgreSQL 17 | 金額は `NUMERIC` 型で誤差ゼロ |
| Excel | openpyxl | xlsx読み込み |
| PDF | Playwright (HTML→PDF) | 明細書レイアウトをHTML/CSSで組む |

---

## 8. テーブル定義（主要部）

全17テーブル。命名は snake_case。実装は `api/app/models/` を参照。

### billing_line — 明細行

```sql
CREATE TABLE billing_line (
  id                 BIGSERIAL PRIMARY KEY,
  period_id          BIGINT NOT NULL REFERENCES billing_period(id) ON DELETE CASCADE,
  order_id           BIGINT NOT NULL REFERENCES rental_order(id),
  item_id            BIGINT NOT NULL REFERENCES item(id),
  statement_id       BIGINT REFERENCES invoice_statement(id) ON DELETE SET NULL,

  item_name_snapshot TEXT NOT NULL,          -- 発行時点の品名を凍結
  delivery_date      DATE,
  return_date        DATE,
  rental_start       DATE,
  rental_end         DATE,
  unit               TEXT,

  -- 編集可能な4項目
  quantity           NUMERIC(12,2) NOT NULL DEFAULT 0,
  base_charge        NUMERIC(12,2),
  unit_price         NUMERIC(12,2),
  duration           NUMERIC(10,2),          -- 日数 または 月換算
  unit_price_type    TEXT NOT NULL,          -- MONTHLY / DAILY / SALE

  -- 金額は生成列。アプリから直接書き込めない
  amount NUMERIC(14,2) GENERATED ALWAYS AS (
      COALESCE(base_charge,0) * quantity
    + COALESCE(unit_price,0) * COALESCE(duration,1) * quantity
  ) STORED,

  -- 取込時の値（手修正の比較用）
  src_quantity       NUMERIC(12,2),
  src_base_charge    NUMERIC(12,2),
  src_unit_price     NUMERIC(12,2),
  src_duration       NUMERIC(10,2),

  is_billable        BOOLEAN NOT NULL DEFAULT TRUE,   -- 値引行は false
  is_provisional     BOOLEAN NOT NULL DEFAULT FALSE,
  display_order      INT,
  seq                INT,
  source_key         TEXT,                   -- 元の識別番号。一意ではない
  deleted_at         TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_line_period    ON billing_line(period_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_line_statement ON billing_line(statement_id);
CREATE INDEX ix_line_item      ON billing_line(item_id);
```

修正済み判定は保存せず、クエリで導出する。

```sql
is_edited = (quantity, base_charge, unit_price, duration)
         IS DISTINCT FROM
            (src_quantity, src_base_charge, src_unit_price, src_duration)
```

### contract — 契約

```sql
CREATE TABLE contract (
  id              BIGSERIAL PRIMARY KEY,
  contract_no     TEXT NOT NULL UNIQUE,
  customer_id     BIGINT NOT NULL REFERENCES customer(id),
  site_id         BIGINT NOT NULL REFERENCES site(id),
  sales_rep_id    BIGINT REFERENCES sales_rep(id),

  skip_statement  BOOLEAN NOT NULL DEFAULT FALSE,  -- 明細不要。洗い替えで消えない
  discount_rate   NUMERIC(5,4),                    -- 将来用。今回は未使用
  note            TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### item — 品目

```sql
CREATE TABLE item (
  id             BIGSERIAL PRIMARY KEY,
  code           TEXT NOT NULL UNIQUE,
  name           TEXT NOT NULL,
  tax_category   TEXT NOT NULL,   -- STANDARD(10%) / REDUCED(8%)
  billing_group  TEXT NOT NULL,   -- EQUIPMENT(備品) / COUNTER(カウンタ)
  is_billable    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**この3列がVBAのハードコード判定を丸ごと吸収する。**

```
If PrdCode = "ＳＲ－ＷＰＥＴ"  → tax_category
If PrdCode Like "*ＰＣＡ*"     → billing_group
If PrdName Like "*値引*"       → is_billable
```

### invoice_statement — 請求明細書

```sql
CREATE TABLE invoice_statement (
  id              BIGSERIAL PRIMARY KEY,
  invoice_id      BIGINT NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,
  contract_id     BIGINT NOT NULL REFERENCES contract(id),
  billing_group   TEXT NOT NULL,          -- EQUIPMENT / COUNTER

  -- 確定時に書き込むスナップショット（確定前は NULL、集計で算出）
  total_ex_tax    NUMERIC(14,2),
  tax_amount      NUMERIC(14,2),
  total_amount    NUMERIC(14,2),

  sort_order      INT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (invoice_id, contract_id, billing_group)
);
```

UNIQUE制約により、同一請求書内で同じ契約×請求グループの明細書が重複生成されることを防ぐ。

### invoice — 請求書

```sql
CREATE TABLE invoice (
  id            BIGSERIAL PRIMARY KEY,
  period_id     BIGINT NOT NULL REFERENCES billing_period(id) ON DELETE CASCADE,
  customer_id   BIGINT NOT NULL REFERENCES customer(id),
  tax_rate      NUMERIC(4,3) NOT NULL,     -- 0.100 / 0.080
  revision      INT NOT NULL DEFAULT 1,    -- 確定のたびに +1

  total_ex_tax  NUMERIC(14,2),
  tax_amount    NUMERIC(14,2),
  total_amount  NUMERIC(14,2),

  status        TEXT NOT NULL DEFAULT 'DRAFT',
  confirmed_at  TIMESTAMPTZ,
  confirmed_by  BIGINT REFERENCES app_user(id),
  issued_at     TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (period_id, customer_id, tax_rate)
);
```

### issued_document — 発行済みPDF

```sql
CREATE TABLE issued_document (
  id         BIGSERIAL PRIMARY KEY,
  period_id  BIGINT NOT NULL REFERENCES billing_period(id),
  invoice_id BIGINT REFERENCES invoice(id),
  doc_type   TEXT NOT NULL,      -- INVOICE / STATEMENT / BUNDLE_ZIP
  revision   INT NOT NULL,       -- 発行時点の版数
  file_path  TEXT NOT NULL,
  file_name  TEXT NOT NULL,      -- 2026-03_請求書_10%_rev2.pdf
  byte_size  BIGINT,
  issued_by  BIGINT NOT NULL REFERENCES app_user(id),
  issued_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_doc_period ON issued_document(period_id, issued_at DESC);
```

**このテーブルのレコードは削除しない。** 洗い替えの対象外。

### line_edit_log — 修正履歴

```sql
CREATE TABLE line_edit_log (
  id        BIGSERIAL PRIMARY KEY,
  line_id   BIGINT NOT NULL REFERENCES billing_line(id) ON DELETE CASCADE,
  field     TEXT NOT NULL,       -- quantity / base_charge / unit_price / duration
  old_value NUMERIC(14,2),
  new_value NUMERIC(14,2),
  edited_by BIGINT NOT NULL REFERENCES app_user(id),
  edited_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reason    TEXT
);
CREATE INDEX ix_editlog_line ON line_edit_log(line_id, edited_at DESC);
```

### period_unlock_log — 確定解除の記録

```sql
CREATE TABLE period_unlock_log (
  id            BIGSERIAL PRIMARY KEY,
  period_id     BIGINT NOT NULL REFERENCES billing_period(id),
  from_revision INT NOT NULL,
  reason        TEXT NOT NULL,   -- 入力必須
  unlocked_by   BIGINT NOT NULL REFERENCES app_user(id),
  unlocked_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 全テーブル一覧

| テーブル | 役割 | 洗い替え |
|---|---|---|
| `office` | 受注営業所 | 対象外 |
| `customer` | 販売先（請求書の宛先） | 対象外 |
| `client` | 得意先（納品先の会社。名寄せ後） | 対象外 |
| `site` | 納品先（現場） | 対象外 |
| `sales_rep` | 営業担当者 | 対象外 |
| `item` | 品目（税区分・請求グループを保持） | 対象外 |
| `contract` | 契約 | 対象外 |
| `billing_period` | 請求期間（状態を保持） | 対象外 |
| `rental_order` | 受注 | **対象** |
| `billing_line` | 明細行 | **対象** |
| `invoice` | 請求書 | **対象** |
| `invoice_statement` | 請求明細書 | **対象** |
| `import_batch` | 取込履歴 | 対象外（追記のみ） |
| `line_edit_log` | 修正履歴 | 対象外（追記のみ） |
| `issued_document` | 発行済みPDF | 対象外（永続保管） |
| `period_unlock_log` | 確定解除の記録 | 対象外（追記のみ） |
| `app_user` | 利用者 | 対象外 |

洗い替え対象は4テーブルのみ、かつ**対象請求期間の分だけ**。

---

## 9. API設計

| メソッド | パス | 役割 |
|---|---|---|
| GET | `/api/periods` | 請求期間の一覧 |
| POST | `/api/periods/{id}/import` | xlsxアップロード。**検証のみ**（投入はしない） |
| POST | `/api/periods/{id}/import/commit` | 洗い替え投入。確定済みなら 409 |
| GET | `/api/periods/{id}/contracts` | **リスト表**。`client` / `site` / `contract_no` / `tax` / `group` で絞り込み |
| PATCH | `/api/contracts/{id}` | `skip_statement` の更新 |
| POST | `/api/periods/{id}/generate` | 明細書・請求書を生成 |
| GET | `/api/statements` | 明細書一覧 |
| GET | `/api/statements/{id}` | 明細書1枚（明細行つき） |
| PATCH | `/api/lines/{id}` | **手修正**。再計算後の3階層の金額を返す |
| POST | `/api/lines/{id}/reset` | 取込時の値に戻す |
| GET | `/api/periods/{id}/invoices` | 請求書（10% / 8%） |
| GET | `/api/periods/{id}/validation` | 発行前チェックの結果 |
| POST | `/api/periods/{id}/confirm` | 確定。合計をスナップショット |
| POST | `/api/periods/{id}/unconfirm` | 確定解除。`reason` 必須。次回確定で版数 +1 |
| POST | `/api/periods/{id}/export` | PDF生成。`issued_document` に登録 |
| GET | `/api/periods/{id}/documents` | 発行済みPDFの一覧（版数つき） |
| GET | `/api/documents/{id}/download` | 過去に発行したPDFの再取得 |
| GET | `/api/periods/{id}/history` | 修正履歴・確定解除の記録 |

### PATCH /api/lines/{id} — 手修正の応答

1リクエストで、変更に伴って動いた金額をすべて返す。画面側で再取得しない。

```jsonc
// Request
{ "quantity": 5 }

// Response
{
  "line":      { "id": 1234, "quantity": 5, "amount": 16500, "is_edited": true },
  "statement": { "id": 77, "total_ex_tax": 148200,
                 "tax_amount": 14820, "total_amount": 163020 },
  "invoice":   { "id": 3,  "total_ex_tax": 2583740,
                 "tax_amount": 258374, "total_amount": 2842114 }
}
```

### 取込の2段階

アップロードと投入を分け、**投入前に必ず内容を確認できる**ようにする。

```jsonc
// POST /import  → 検証のみ。DBは変更しない
{
  "rows": 937,
  "period": "2025-03-01〜2025-03-31",
  "customer": "架空事務機株式会社 東日本営業所",
  "errors": [],
  "warnings": [
    { "type": "UNKNOWN_ITEM",
      "message": "未登録の品番 DM-155 が3行あります。税区分を確認してください" }
  ]
}

// POST /import/commit  → 洗い替えを実行（本文なし）
```

### 検証で返すもの

| 種別 | 条件 | 扱い |
|---|---|---|
| `FORMAT` | 列構成が49列と異なる | エラー・投入不可 |
| `MULTI_PERIOD` | 請求期間が複数混在 | エラー・絞り込み条件の誤り |
| `MULTI_CUSTOMER` | 販売先が複数混在 | エラー |
| `PERIOD_LOCKED` | 対象期間が確定済み | エラー・確定解除が必要 |
| `UNKNOWN_ITEM` | 未登録の品番が出現 | 警告・税区分の確認を促す |
| `ZERO_PRICE` | 単価が0または空 | 警告・投入は可能 |
| `NEW_CONTRACT` | 前月になかった契約 | 情報・新規現場の把握 |
| `MISSING_CONTRACT` | 前月にあって今月ない契約 | 情報・**取り忘れの検知** |

最後の2つは前月データが残っていることが前提。履歴を保持する設計の利点。

---

## 10. 非機能要件

| 項目 | 要件 |
|---|---|
| 処理性能 | 1,000行の取込・生成を10秒以内（現行VBAは数十秒） |
| 手修正の応答 | 1セルの編集から画面反映まで 500ms 以内 |
| 同時利用 | 数名を想定。同一請求期間の同時編集は楽観ロックで検出 |
| 金額の正確性 | `NUMERIC` / `Decimal` を全経路で使用。**浮動小数点を使わない** |
| データ保持 | **過去月は削除しない**。937行/月 × 12 ≒ 1.1万行/年。5年で5.6万行 |
| PDF保管 | 発行済みPDFはファイルシステムに永続保管。バックアップ対象 |
| バックアップ | 日次で `pg_dump`。確定済み期間は変更されないため差分が小さい |
| 監査 | 手修正・確定・確定解除・取込をすべて記録。削除は論理削除 |
| 権限 | 閲覧のみ／編集可／確定可 の3段階 |
| データ配置 | 取引先情報を含むため、当面は社内サーバーで運用 |
| ブラウザ | Chrome / Edge の最新版 |

---

## 11. 開発フェーズ

各フェーズの終わりに、動くものが手元に残る単位で区切る。

| # | フェーズ | 成果物 | 完了条件 |
|---|---|---|---|
| 1 | 基盤構築 | Docker Compose / DDL / Alembic / FastAPI・Next.js の雛形 | `docker compose up` で3層が起動 |
| 2 | 取込 | F-01 / F-02、P-01・P-02 | `fixtures/202603.xlsx` を投入し937行がDBに入る |
| 3 | リスト表 | F-03、P-03 | 3条件のフィルタが動き、明細不要が翌月へ継承される |
| 4 | 生成ロジック | F-04 / F-06 | **VBAと同じ数値が出る**（下の受け入れ基準） |
| 5 | 手修正 | F-05 / F-10、P-04・P-05・P-09 | セル編集で3階層の合計が即時連動 |
| 6 | PDF・確定 | F-08 / F-09 / F-11、P-06・P-08・P-10 | 現行と同等のレイアウトで出力・版数管理 |
| 7 | チェック機能 | F-07、P-07 | 請求漏れを検出できる |

### フェーズ4の受け入れ基準

現行VBAで生成した以下の数値が一致すれば、ロジック移植は成功と判定する。

| 検証項目 | 期待値 |
|---|---|
| 契約数 | 81 件（うちカウンタのみ 1件） |
| 請求先会社数 | 5 社 |
| 10% 税抜合計 | 2,583,740 円 |
| 8% 税抜合計 | 35,000 円 |
| 請求対象外（値引） | 10 行 / −74,977 円 |
| 明細書 枚数 | 10%側 84ページ＋カウンタ分 / 8%側 4枚 |

検証は `/acceptance-check` スキルで実行できる。
