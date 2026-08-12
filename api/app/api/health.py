"""疎通確認。フェーズ1の完了判定に使う。"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Base

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/api/health/db")
def health_db(db: Session = Depends(get_db)) -> dict:
    """DB接続と、テーブルが作られているかを確認する。

    定義済みテーブルのうち何個が実在するかを返す。
    マイグレーション未適用なら existing が 0 になる。
    """
    version = db.execute(select(func.version())).scalar_one()

    inspector = inspect(db.get_bind())
    actual = set(inspector.get_table_names())
    expected = set(Base.metadata.tables.keys())

    return {
        "status": "ok",
        "postgres": version.split(" on ")[0],
        "tables": {
            "expected": len(expected),
            "existing": len(expected & actual),
            "missing": sorted(expected - actual),
        },
    }
