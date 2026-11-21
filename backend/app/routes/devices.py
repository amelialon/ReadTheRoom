from uuid import UUID

from fastapi import APIRouter, Depends
from psycopg import Connection

from ..auth import require_dashboard, require_device
from ..db import get_conn
from ..schemas import DeviceList, DeviceOut, SensorLatest
from .. import store

router = APIRouter(prefix="/api/devices", tags=["Devices"])


@router.get("", response_model=DeviceList)
def get_devices(
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_dashboard),
):
    return {"devices": store.list_devices(conn)}


@router.post("/{device_id}/heartbeat", response_model=DeviceOut)
def post_heartbeat(
    device_id: UUID,
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_device),
):
    return store.heartbeat(conn, device_id)


@router.get("/{device_id}/sensor-readings/latest", response_model=SensorLatest)
def get_latest_reading(
    device_id: UUID,
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_dashboard),
):
    return {"reading": store.latest_reading(conn, device_id)}
