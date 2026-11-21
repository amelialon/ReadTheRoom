from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from psycopg import Connection

from ..auth import require_dashboard, require_device, require_either
from ..db import get_conn
from ..schemas import RequestIn, RequestList, RequestOut, RequestUpdate
from .. import store

router = APIRouter(prefix="/api/requests", tags=["Requests"])


@router.post(
    "",
    response_model=RequestOut,
    status_code=201,
    responses={200: {"model": RequestOut}, 201: {"model": RequestOut}},
)
def create_request(
    body: RequestIn,
    response: Response,
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_device),
):
    row, created = store.save_request(conn, body)
    response.status_code = 201 if created else 200
    return row


@router.get("", response_model=RequestList)
def get_requests(
    state: Optional[str] = Query(default=None, pattern="^(pending|escalated|acked)$"),
    patient_id: Optional[UUID] = None,
    device_id: Optional[UUID] = None,
    limit: int = Query(default=100, ge=1, le=200),
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_either),
):
    return {"requests": store.list_requests(conn, state, patient_id, device_id, limit)}


@router.get("/{request_id}", response_model=RequestOut)
def get_request(
    request_id: UUID,
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_either),
):
    return store.get_request(conn, request_id)


@router.patch("/{request_id}", response_model=RequestOut)
def patch_request(
    request_id: UUID,
    body: RequestUpdate,
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_dashboard),
):
    return store.acknowledge(conn, request_id)
