"""共通の依存性。

認証はフェーズ6以降で入れる。それまでは単一の既定ユーザーで動かすが、
手修正・確定・取込の記録に実行者が必要なので、いま器だけ用意しておく。
後で認証を差し込むときに、この関数の中身を差し替えるだけで済む。
"""

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser
from app.models.base import UserRole

DEFAULT_LOGIN_ID = "system"


def get_current_user(db: Session = Depends(get_db)) -> AppUser:
    user = db.execute(
        select(AppUser).where(AppUser.login_id == DEFAULT_LOGIN_ID)
    ).scalar_one_or_none()
    if user is None:
        user = AppUser(
            login_id=DEFAULT_LOGIN_ID,
            display_name="システム",
            role=UserRole.APPROVER,
        )
        db.add(user)
        db.flush()
    return user
