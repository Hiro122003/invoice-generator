"""F-09 PDF出力APIの入出力。"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class IssuedFileOut(BaseModel):
    doc_type: str
    invoice_id: int | None
    revision: int
    file_name: str
    byte_size: int


class ExportResultOut(BaseModel):
    period_id: int
    files: list[IssuedFileOut]


class IssuedDocumentOut(BaseModel):
    id: int
    period_id: int
    invoice_id: int | None
    doc_type: str
    revision: int
    file_name: str
    byte_size: int | None
    issued_by_name: str
    issued_at: dt.datetime
