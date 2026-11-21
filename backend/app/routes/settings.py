from fastapi import APIRouter, Depends
from psycopg import Connection

from ..auth import require_either
from ..db import get_conn
from ..schemas import SettingsOut
from .. import store

router = APIRouter(prefix="/api/settings", tags=["Settings"])


@router.get("", response_model=SettingsOut)
def read_settings(
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_either),
):
    return store.get_settings(conn)
