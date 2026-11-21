from fastapi import APIRouter, Depends
from psycopg import Connection

from ..auth import require_device
from ..db import get_conn
from ..schemas import SensorIn, SensorOut
from .. import store

router = APIRouter(prefix="/api/sensor-readings", tags=["Sensors"])


@router.post("", response_model=SensorOut, status_code=201)
def create_reading(
    body: SensorIn,
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_device),
):
    return store.save_reading(conn, body)
