# invoice-generator

建設現場向け機材レンタル業の月次請求業務を、Excelマクロ（VBA 約6,100行）から
Webアプリケーションへ移行するプロジェクト。

Excelで運用されていた請求書・請求明細書の発行を、取込から手修正、PDF発行、
締め処理までブラウザ上で完結させる。**実務投入を前提**とする。

## 背景

月次で数十件の建設現場に対しレンタル機材を請求する業務があり、
基幹システムから出力したExcelを元に、VBAマクロで請求書と請求明細書を生成していた。

このマクロには次の問題があった。

- 帳票の紙面構造（1ページ44行）がロジック全体に定数として浸透し、テンプレートを1行変えると破綻する
- 8%用と10%用でほぼ同一の1,700行が重複し、修正が片方だけに入るなど不整合が生じる
- 「この現場は明細書不要」といった設定がシート上の直書きで、実行のたびに消える
- 取込データの検証がなく、請求漏れに気づけない

これらを構造から解消する。

## 技術スタック

```
Next.js (App Router / TypeScript)   画面
   │ HTTP / JSON
FastAPI + SQLAlchemy + Alembic      取込・計算・PDF生成
   │ SQL
PostgreSQL 17 (Docker)              データ
```

Excel取込は openpyxl、PDF生成は Playwright（HTML → PDF）。
金額は全経路で `NUMERIC` / `Decimal` を使い、浮動小数点を経由させない。

## ドキュメント

| 文書 | 内容 |
|---|---|
| [docs/design.md](docs/design.md) | 要件定義・基本設計書 |
| [docs/domain-model.md](docs/domain-model.md) | ドメインモデル。元データ49列の行き先 |
| [docs/vba-analysis.md](docs/vba-analysis.md) | 移行元VBAの実装分析 |
| [CLAUDE.md](CLAUDE.md) | 開発規約・破ってはいけないルール |

## 開発

```bash
cp .env.example .env                              # 初回のみ
docker compose up -d                              # 3層を起動
docker compose exec api alembic upgrade head      # マイグレーション
docker compose exec api pytest                    # テスト
```

起動後の確認先。

| | |
|---|---|
| 画面 | http://localhost:3000 |
| API | http://localhost:8000/api/health/db |
| API仕様（自動生成） | http://localhost:8000/docs |

## 進捗

| # | フェーズ | 状態 |
|---|---|---|
| 1 | 基盤構築（Docker / DDL / 雛形） | **完了** |
| 2 | 取込 | — |
| 3 | リスト表 | — |
| 4 | 生成ロジック | — |
| 5 | 手修正 | — |
| 6 | PDF・確定 | — |
| 7 | 発行前チェック | — |

フェーズ4の完了時は、現行VBAと同じ金額が出ることを受け入れ基準として検証する。

## データの取り扱い

**Excel・CSV・PDF はリポジトリに含めない。**

基幹システムから出力されるデータは実在の取引先名・現場名・金額を含む。
匿名化したつもりでも、セル以外（図形・共有文字列・保存元パスなどのメタデータ）に
情報が残ることがあり、確認漏れが起きやすい。

そのため `.gitignore` で `*.xlsx` `*.xlsm` `*.xls` `*.csv` `*.pdf` および
`fixtures/` を例外なく除外している。開発に使う参照データは各自のローカルに置く。
