"""データベース接続。

金額は NUMERIC で保持し、Python 側では Decimal のまま扱う。
SQLAlchemy の Numeric 型は既定で Decimal を返すため、float への変換は起きない。
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,  # 長時間アイドル後の切断を検出して張り直す
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI の依存性注入用。リクエストごとにセッションを開閉する。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
