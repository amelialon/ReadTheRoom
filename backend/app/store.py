"""SQL for the central tables. Callers hold one transaction per write."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import HTTPException
from psycopg import Connection
from psycopg.errors import CheckViolation, ForeignKeyViolation

from .schemas import RequestIn, SensorIn

_REQUEST = """
select
  r.id, r.local_id, r.patient_id, r.device_id,
  p.display_name, p.bed,
  r.message, r.urgent, r.state,
  r.created_at, r.received_at, r.acked_at,
  r.category, r.detected_object, r.context,
  greatest(
    0,
    floor(extract(epoch from (coalesce(r.acked_at, now()) - r.created_at)))
  )::bigint as waiting_s,
  (
    r.state <> 'acked'
    and (
      (r.state = 'escalated' and not r.urgent)
      or r.created_at <= now() - make_interval(secs => s.escalate_after_s)
    )
  ) as overdue
from requests as r
join patients as p on p.id = r.patient_id
cross join app_settings as s
"""

def save_request(conn: Connection, body: RequestIn) -> tuple:
    """Insert the device UUID once. A retry returns the current row and does not reopen it."""
    with conn.transaction():
        _require_assignment(conn, body.patient_id, body.device_id)
        inserted = conn.execute(
            """
            insert into requests (
              id, local_id, patient_id, device_id, message, urgent, state,
              created_at, acked_at, category, detected_object, context
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            on conflict (id) do nothing
            returning id
            """,
            (
                body.id, body.local_id, body.patient_id, body.device_id, body.message.strip(),
                body.urgent, body.state, body.created_at, body.acked_at, body.category,
                body.detected_object, json.dumps(body.context),
            ),
        ).fetchone()
        if inserted:
            return _one_request(conn, body.id), True

        current = conn.execute(
            """
            select
              local_id is not distinct from %s as local_ok,
              patient_id is not distinct from %s as patient_ok,
              device_id is not distinct from %s as device_ok,
              message is not distinct from %s as message_ok,
              urgent is not distinct from %s as urgent_ok,
              created_at is not distinct from %s as created_ok,
              category is not distinct from %s as category_ok,
              detected_object is not distinct from %s as object_ok,
              context is not distinct from %s::jsonb as context_ok,
              state
            from requests
            where id = %s
            for update
            """,
            (
                body.local_id, body.patient_id, body.device_id, body.message.strip(),
                body.urgent, body.created_at, body.category, body.detected_object,
                json.dumps(body.context), body.id,
            ),
        ).fetchone()
        if current is None:
            raise HTTPException(status_code=409, detail="Request id already exists with different details")
        if not all(current[name] for name in (
            "local_ok", "patient_ok", "device_ok", "message_ok", "urgent_ok",
            "created_ok", "category_ok", "object_ok", "context_ok",
        )):
            raise HTTPException(status_code=409, detail="Request id already exists with different details")

        incoming = body.state
        stored = current["state"]
        if stored == "pending" and incoming == "escalated":
            conn.execute(
                """
                update requests
                set state = 'escalated'
                where id = %s and state = 'pending'
                """,
                (body.id,),
            )
        elif incoming == "acked" and stored != "acked":
            raise HTTPException(
                status_code=403,
                detail="Acknowledging a stored request requires the dashboard token",
            )
        return _one_request(conn, body.id), False


def acknowledge(conn: Connection, request_id) -> dict:
    """Nurse acknowledgement. A repeat keeps the original acked_at."""
    with conn.transaction():
        conn.execute(
            """
            update requests
            set state = 'acked', acked_at = now()
            where id = %s and state <> 'acked'
            """,
            (request_id,),
        )
        row = _find_request(conn, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Request not found")
        return row


def list_requests(conn: Connection, state: Optional[str], patient_id, device_id, limit: int) -> list:
    clauses = []
    params = []
    if state is not None:
        clauses.append("r.state = %s")
        params.append(state)
    if patient_id is not None:
        clauses.append("r.patient_id = %s")
        params.append(patient_id)
    if device_id is not None:
        clauses.append("r.device_id = %s")
        params.append(device_id)
    where = (" where " + " and ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        _REQUEST + where + " order by r.created_at desc limit %s",
        params,
    ).fetchall()
    return [_request(row) for row in rows]


def get_request(conn: Connection, request_id) -> dict:
    row = _find_request(conn, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return row


def list_patients(conn: Connection) -> list:
    return conn.execute(
        """
        select id, display_name, bed, active, created_at
        from patients
        order by bed, display_name
        """
    ).fetchall()


def get_patient(conn: Connection, patient_id) -> dict:
    row = conn.execute(
        """
        select id, display_name, bed, active, created_at
        from patients where id = %s
        """,
        (patient_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return row


def list_devices(conn: Connection) -> list:
    return conn.execute(_DEVICE + " order by p.bed, d.name").fetchall()


def heartbeat(conn: Connection, device_id) -> dict:
    with conn.transaction():
        updated = conn.execute(
            """
            update devices
            set last_heartbeat_at = now()
            where id = %s
            returning id
            """,
            (device_id,),
        ).fetchone()
        if updated is None:
            raise HTTPException(status_code=404, detail="Device not found")
        return _one_device(conn, device_id)


def save_reading(conn: Connection, body: SensorIn) -> dict:
    try:
        with conn.transaction():
            if _device(conn, body.device_id) is None:
                raise HTTPException(status_code=404, detail="Device not found")
            row = conn.execute(
                """
                insert into sensor_readings (device_id, temperature_c, humidity_pct, observed_at)
                values (%s, %s, %s, %s)
                returning id, device_id, temperature_c, humidity_pct, observed_at
                """,
                (body.device_id, body.temperature_c, body.humidity_pct, body.observed_at),
            ).fetchone()
            return row
    except CheckViolation:
        raise HTTPException(status_code=422, detail="Sensor reading is outside the accepted range")
    except ForeignKeyViolation:
        raise HTTPException(status_code=404, detail="Device not found")


def latest_reading(conn: Connection, device_id) -> Optional[dict]:
    if _device(conn, device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return conn.execute(
        """
        select id, device_id, temperature_c, humidity_pct, observed_at
        from sensor_readings
        where device_id = %s
        order by observed_at desc, id desc
        limit 1
        """,
        (device_id,),
    ).fetchone()


def get_settings(conn: Connection) -> dict:
    row = conn.execute(
        """
        select heartbeat_interval_s, offline_after_s, escalate_after_s
        from app_settings where id = 1
        """
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="Server settings are missing")
    return row


_DEVICE = """
select
  d.id, d.patient_id, d.name, p.display_name, p.bed,
  d.last_heartbeat_at, d.created_at,
  (
    d.last_heartbeat_at is not null
    and d.last_heartbeat_at >= now() - make_interval(secs => s.offline_after_s)
  ) as online
from devices as d
join patients as p on p.id = d.patient_id
cross join app_settings as s
"""


def _require_assignment(conn: Connection, patient_id, device_id) -> None:
    device = _device(conn, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    patient = conn.execute("select 1 from patients where id = %s", (patient_id,)).fetchone()
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if str(device["patient_id"]) != str(patient_id):
        raise HTTPException(status_code=403, detail="Device is not assigned to this patient")


def _device(conn: Connection, device_id):
    return conn.execute(
        "select id, patient_id from devices where id = %s",
        (device_id,),
    ).fetchone()


def _find_request(conn: Connection, request_id):
    return _request_or_none(conn.execute(
        _REQUEST + " where r.id = %s",
        (request_id,),
    ).fetchone())


def _one_request(conn: Connection, request_id) -> dict:
    row = _find_request(conn, request_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Request could not be read back")
    return row


def _one_device(conn: Connection, device_id) -> dict:
    row = conn.execute(_DEVICE + " where d.id = %s", (device_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return row


def _request_or_none(row):
    if row is None:
        return None
    return _request(row)


def _request(row) -> dict:
    data = dict(row)
    context = data.get("context")
    if isinstance(context, str):
        data["context"] = json.loads(context)
    elif context is None:
        data["context"] = {}
    return data
