"""F-09 PDF出力。生成・一覧・再ダウンロード。

発行済みPDFは削除しない（洗い替えの対象外）。「先方に何を送ったか」の正は
DBの数値ではなくこのPDFそのもの（docs/design.md）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import AppUser, IssuedDocument
from app.schemas.export import ExportResultOut, IssuedDocumentOut, IssuedFileOut
from app.services import export as export_service

router = APIRouter(prefix="/api", tags=["export"])


@router.post("/periods/{period_id}/export", response_model=ExportResultOut)
def export_period(
    period_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
) -> ExportResultOut:
    try:
        summary = export_service.export_period(db, period_id, user)
    except export_service.PeriodNotFoundError as e:
        db.rollback()
        raise HTTPException(404, str(e)) from e
    except export_service.NoInvoicesError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e

    db.commit()
    return ExportResultOut(
        period_id=summary.period_id,
        files=[
            IssuedFileOut(
                doc_type=f.doc_type,
                invoice_id=f.invoice_id,
                revision=f.revision,
                file_name=f.file_name,
                byte_size=f.byte_size,
            )
            for f in summary.files
        ],
    )


@router.get("/periods/{period_id}/documents", response_model=list[IssuedDocumentOut])
def list_documents(period_id: int, db: Session = Depends(get_db)) -> list[IssuedDocumentOut]:
    rows = db.execute(
        text(
            """
            SELECT d.id, d.period_id, d.invoice_id, d.doc_type, d.revision,
                   d.file_name, d.byte_size, u.display_name AS issued_by_name, d.issued_at
            FROM issued_document d
            JOIN app_user u ON u.id = d.issued_by
            WHERE d.period_id = :period_id
            ORDER BY d.issued_at DESC
            """
        ),
        {"period_id": period_id},
    ).mappings().all()
    return [IssuedDocumentOut(**dict(r)) for r in rows]


@router.get("/documents/{document_id}/download")
def download_document(document_id: int, db: Session = Depends(get_db)) -> FileResponse:
    doc = db.get(IssuedDocument, document_id)
    if doc is None:
        raise HTTPException(404, "発行済み書類が見つかりません。")
    path = Path(doc.file_path)
    if not path.exists():
        # ファイルシステム上の実体が無い場合。DBの記録はあるのに
        # ファイルが消えているのは運用事故なので、握りつぶさず明示する。
        raise HTTPException(410, "ファイルの実体が見つかりません。")
    return FileResponse(
        path=path,
        filename=doc.file_name,
        media_type="application/pdf" if doc.file_name.endswith(".pdf") else "application/zip",
    )
