"""FastAPI エントリポイント。

フェーズ1では疎通確認のみ。業務APIはフェーズ2以降で追加する。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, imports, periods
from app.config import settings

app = FastAPI(
    title="請求書生成システム API",
    description=(
        "建設現場向け機材レンタル業の月次請求業務。"
        "Excel取込・請求書/請求明細書の生成・手修正・PDF発行を扱う。"
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(periods.router)
app.include_router(imports.router)
