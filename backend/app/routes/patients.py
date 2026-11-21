from uuid import UUID

from fastapi import APIRouter, Depends
from psycopg import Connection

from ..auth import require_dashboard
from ..db import get_conn
from ..schemas import PatientList, PatientOut
from .. import store

router = APIRouter(prefix="/api/patients", tags=["Patients"])


@router.get("", response_model=PatientList)
def get_patients(
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_dashboard),
):
    return {"patients": store.list_patients(conn)}


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(
    patient_id: UUID,
    conn: Connection = Depends(get_conn),
    _: None = Depends(require_dashboard),
):
    return store.get_patient(conn, patient_id)
