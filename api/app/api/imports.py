"""Excel取込。

アップロードと投入を2段階に分ける。投入前に必ず内容を確認できるようにするため。

    POST /api/imports/validate   検証だけ。DBは変更しない
    POST /api/imports            洗い替え投入

どちらもファイルを受け取る。サーバー側に一時ファイルを持たない設計にして、
「検証したファイルと投入したファイルが違う」という事故を避ける
（投入時にも必ず再検証する）。
"""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import AppUser, BillingLine, BillingPeriod
from app.schemas.imports import ImportResultOut, IssueOut, ValidationOut
from app.services import excel_reader, importer

router = APIRouter(prefix="/api/imports", tags=["imports"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 実データは約200KB。桁違いに大きいものは弾く


async def _read_upload(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=415,
            detail="xlsx または xlsm ファイルを指定してください。",
        )
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"ファイルが大きすぎます（上限 {MAX_UPLOAD_BYTES // 1024 // 1024}MB）。",
        )
    if not content:
        raise HTTPException(status_code=400, detail="ファイルが空です。")
    return content


def _build_validation(
    db: Session, result: excel_reader.ParseResult, file_name: str
) -> ValidationOut:
    out = ValidationOut(
        file_name=file_name,
        rows=len(result.rows),
        period_start=result.period_start,
        period_end=result.period_end,
        period_label=result.period_start.strftime("%Y-%m")
        if result.period_start
        else None,
        customer_name=result.customer_name,
        contracts=len({r.contract_no for r in result.rows}),
        clients=len({r.client_name for r in result.rows}),
        sites=len({r.site_name for r in result.rows}),
        items=len({r.item_code for r in result.rows}),
        issues=[IssueOut(**vars(i)) for i in result.issues],
    )

    if result.rows:
        deleted_in_source = sum(1 for r in result.rows if r.is_deleted_in_source)
        if deleted_in_source:
            out.issues.append(
                IssueOut(
                    severity=excel_reader.Severity.INFO,
                    type="DELETED_IN_SOURCE",
                    message=(
                        f"基幹システム側で削除された明細が {deleted_in_source} 件あります。"
                        "取り込むと論理削除として保持され、請求対象からは除外されます。"
                    ),
                )
            )

        out.unknown_items = importer.find_unknown_item_codes(db, result)
        if out.unknown_items:
            out.issues.append(
                IssueOut(
                    severity=excel_reader.Severity.WARNING,
                    type="UNKNOWN_ITEM",
                    message=(
                        f"品目マスタに未登録の品番が {len(out.unknown_items)} 件あります。"
                        "税区分（8%/10%）と請求グループ（備品/カウンタ）の判定を"
                        "確認してください。"
                    ),
                )
            )

    # 同じ請求期間が既にあるか。確定済みなら投入できない
    if result.period_start and result.period_end:
        period = db.execute(
            select(BillingPeriod).where(
                BillingPeriod.start_date == result.period_start,
                BillingPeriod.end_date == result.period_end,
            )
        ).scalar_one_or_none()
        if period:
            out.period_exists = True
            out.period_status = period.status
            out.existing_lines = (
                db.execute(
                    select(func.count())
                    .select_from(BillingLine)
                    .where(BillingLine.period_id == period.id)
                ).scalar_one()
                or 0
            )
            if period.is_locked:
                out.issues.append(
                    IssueOut(
                        severity=excel_reader.Severity.ERROR,
                        type="PERIOD_LOCKED",
                        message=(
                            f"{period.label} は確定済みのため投入できません。"
                            "訂正する場合は確定解除してください。"
                        ),
                    )
                )
            elif out.existing_lines:
                out.issues.append(
                    IssueOut(
                        severity=excel_reader.Severity.INFO,
                        type="WILL_REPLACE",
                        message=(
                            f"{period.label} には既に {out.existing_lines:,} 行あります。"
                            "投入するとこの期間のデータは入れ替わります"
                            "（他の月には影響しません）。"
                        ),
                    )
                )

    out.can_import = not any(
        i.severity == excel_reader.Severity.ERROR for i in out.issues
    )
    return out


@router.post("/validate", response_model=ValidationOut)
async def validate_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ValidationOut:
    """検証のみ。DBは一切変更しない。"""
    content = await _read_upload(file)
    result = excel_reader.parse(io.BytesIO(content))
    return _build_validation(db, result, file.filename or "")


@router.post("", response_model=ImportResultOut)
async def commit_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
) -> ImportResultOut:
    """洗い替え投入。対象請求期間の明細だけを入れ替える。"""
    content = await _read_upload(file)
    result = excel_reader.parse(io.BytesIO(content))

    validation = _build_validation(db, result, file.filename or "")
    if not validation.can_import:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "検証エラーがあるため投入できません。",
                "issues": [i.model_dump() for i in validation.issues],
            },
        )

    try:
        summary = importer.run_import(db, result, file.filename or "", user)
    except importer.PeriodLockedError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except importer.ValidationFailedError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e

    db.commit()

    return ImportResultOut(
        period_id=summary.period_id,
        period_label=summary.period_start.strftime("%Y-%m"),
        inserted_lines=summary.inserted_lines,
        deleted_lines=summary.deleted_lines,
        deleted_in_source=summary.deleted_in_source,
        orders=summary.orders,
        contracts=summary.contracts,
        clients=summary.clients,
        sites=summary.sites,
        items=summary.items,
        new_items=summary.new_items,
    )
