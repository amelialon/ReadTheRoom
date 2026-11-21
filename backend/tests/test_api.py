"""Endpoint tests. They use the temporary Postgres from conftest, never Supabase."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import RequestIn
from app.settings import Settings
from app import store

PATIENT = "11111111-1111-4111-8111-111111111111"
DEVICE = "22222222-2222-4222-8222-222222222222"
DEVICE_TOKEN = "device-test-token"
DASHBOARD_TOKEN = "dashboard-test-token"
FIELDS = {
    "id", "local_id", "patient_id", "device_id", "display_name", "bed", "message",
    "urgent", "state", "created_at", "received_at", "acked_at", "category",
    "detected_object", "context", "waiting_s", "overdue",
}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _body(**overrides):
    body = {
        "id": str(uuid4()),
        "local_id": 1,
        "patient_id": PATIENT,
        "device_id": DEVICE,
        "message": "Bed 4: patient is thirsty and wants water",
        "urgent": False,
        "state": "pending",
        "created_at": "2026-11-21T15:30:00Z",
        "acked_at": None,
        "category": None,
        "detected_object": None,
        "context": {},
    }
    body.update(overrides)
    return body


@pytest.fixture
def api(db):
    assert db.test_dsn.startswith("postgresql://")
    assert "127.0.0.1" in db.test_dsn
    assert "supabase.co" not in db.test_dsn
    settings = Settings(
        database_url=db.test_dsn,
        device_api_token=DEVICE_TOKEN,
        dashboard_api_token=DASHBOARD_TOKEN,
        cors_origins="http://localhost:5173",
        port=8001,
    )
    application = create_app(settings)
    with TestClient(application) as client:
        yield client
    db.execute("delete from sensor_readings")
    db.execute("delete from requests")
    db.execute(
        "update devices set last_heartbeat_at = null where id = %s",
        (DEVICE,),
    )


def test_health_is_public_and_has_no_secrets(api):
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert "postgres" not in response.text
    assert "token" not in response.text
    allowed = api.get("/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_missing_and_unknown_tokens_are_rejected(api):
    assert api.get("/api/requests").status_code == 401
    assert api.get("/api/requests", headers=_auth("nope")).status_code == 401


def test_tokens_cannot_cross_roles(api):
    created = api.post("/api/requests", headers=_auth(DEVICE_TOKEN), json=_body())
    assert created.status_code == 201
    request_id = created.json()["id"]
    assert api.post("/api/requests", headers=_auth(DASHBOARD_TOKEN), json=_body()).status_code == 403
    assert api.patch(
        f"/api/requests/{request_id}",
        headers=_auth(DEVICE_TOKEN),
        json={"state": "acked"},
    ).status_code == 403
    assert api.get("/api/patients", headers=_auth(DEVICE_TOKEN)).status_code == 403
    assert api.post(f"/api/devices/{DEVICE}/heartbeat", headers=_auth(DASHBOARD_TOKEN)).status_code == 403


def test_create_is_idempotent_and_conflict_rejects_a_different_body(api):
    payload = _body()
    first = api.post("/api/requests", headers=_auth(DEVICE_TOKEN), json=payload)
    second = api.post("/api/requests", headers=_auth(DEVICE_TOKEN), json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert set(first.json()) == FIELDS
    assert set(second.json()) == FIELDS
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["received_at"] == second.json()["received_at"]
    listed = api.get("/api/requests", headers=_auth(DASHBOARD_TOKEN)).json()["requests"]
    assert len([row for row in listed if row["id"] == payload["id"]]) == 1

    conflict = dict(payload, message="Bed 4: patient needs the toilet")
    rejected = api.post("/api/requests", headers=_auth(DEVICE_TOKEN), json=conflict)
    assert rejected.status_code == 409
    stored = api.get(f"/api/requests/{payload['id']}", headers=_auth(DASHBOARD_TOKEN))
    assert stored.status_code == 200
    assert stored.json()["message"] == payload["message"]


def test_escalation_and_acknowledgement_do_not_reopen_or_move_the_timestamp(api):
    payload = _body()
    assert api.post("/api/requests", headers=_auth(DEVICE_TOKEN), json=payload).status_code == 201
    escalated = api.post(
        "/api/requests",
        headers=_auth(DEVICE_TOKEN),
        json=dict(payload, state="escalated"),
    )
    assert escalated.status_code == 200
    assert escalated.json()["state"] == "escalated"
    assert escalated.json()["overdue"] is True

    older = api.post("/api/requests", headers=_auth(DEVICE_TOKEN), json=payload)
    assert older.status_code == 200
    assert older.json()["state"] == "escalated"

    acked = api.patch(
        f"/api/requests/{payload['id']}",
        headers=_auth(DASHBOARD_TOKEN),
        json={"state": "acked"},
    )
    assert acked.status_code == 200
    assert set(acked.json()) == FIELDS
    again = api.patch(
        f"/api/requests/{payload['id']}",
        headers=_auth(DASHBOARD_TOKEN),
        json={"state": "acked"},
    )
    assert again.status_code == 200
    assert again.json()["acked_at"] == acked.json()["acked_at"]
    assert again.json()["state"] == "acked"

    reopened = api.post("/api/requests", headers=_auth(DEVICE_TOKEN), json=payload)
    assert reopened.status_code == 200
    assert reopened.json()["state"] == "acked"
    assert reopened.json()["acked_at"] == acked.json()["acked_at"]
    assert api.patch(
        f"/api/requests/{payload['id']}",
        headers=_auth(DASHBOARD_TOKEN),
        json={"state": "pending"},
    ).status_code == 422


def test_device_cannot_acknowledge_a_stored_request_by_posting(api):
    payload = _body()
    assert api.post("/api/requests", headers=_auth(DEVICE_TOKEN), json=payload).status_code == 201
    rejected = api.post(
        "/api/requests",
        headers=_auth(DEVICE_TOKEN),
        json=dict(payload, state="acked", acked_at="2026-11-21T15:40:00Z"),
    )
    assert rejected.status_code == 403
    stored = api.get(f"/api/requests/{payload['id']}", headers=_auth(DEVICE_TOKEN))
    assert stored.json()["state"] == "pending"
    assert stored.json()["acked_at"] is None


def test_first_insert_may_already_be_acknowledged(api):
    payload = _body(state="acked", acked_at="2026-11-21T15:40:00Z", urgent=True)
    created = api.post("/api/requests", headers=_auth(DEVICE_TOKEN), json=payload)
    assert created.status_code == 201
    assert created.json()["state"] == "acked"
    assert created.json()["acked_at"].startswith("2026-11-21T15:40:00")


def test_a_device_cannot_file_a_request_for_another_patient(api, db):
    other = str(uuid4())
    db.execute(
        "insert into patients (id, display_name, bed) values (%s, %s, %s)",
        (other, "Other Patient", "Bed 9"),
    )
    rejected = api.post(
        "/api/requests",
        headers=_auth(DEVICE_TOKEN),
        json=_body(patient_id=other),
    )
    assert rejected.status_code == 403
    db.execute("delete from patients where id = %s", (other,))


def test_invalid_request_bodies_and_unknown_records(api):
    assert api.post(
        "/api/requests",
        headers=_auth(DEVICE_TOKEN),
        json=_body(urgent=True, state="pending"),
    ).status_code == 422
    assert api.post(
        "/api/requests",
        headers=_auth(DEVICE_TOKEN),
        json=_body(local_id=2147483648),
    ).status_code == 422
    old = api.post(
        "/api/requests",
        headers=_auth(DEVICE_TOKEN),
        json=_body(created_at="1900-01-01T00:00:00Z"),
    )
    assert old.status_code == 201
    assert old.json()["waiting_s"] > 2_000_000_000
    assert api.post(
        "/api/requests",
        headers=_auth(DEVICE_TOKEN),
        json=_body(state="resolved"),
    ).status_code == 422
    other_patient = str(uuid4())
    assert api.post(
        "/api/requests",
        headers=_auth(DEVICE_TOKEN),
        json=_body(patient_id=other_patient),
    ).status_code == 404
    missing_device = str(uuid4())
    assert api.post(
        f"/api/devices/{missing_device}/heartbeat",
        headers=_auth(DEVICE_TOKEN),
    ).status_code == 404
    assert api.get(f"/api/requests/{uuid4()}", headers=_auth(DASHBOARD_TOKEN)).status_code == 404
    assert api.get(f"/api/patients/{uuid4()}", headers=_auth(DASHBOARD_TOKEN)).status_code == 404


def test_patients_devices_settings_heartbeat_and_sensors(api):
    patients = api.get("/api/patients", headers=_auth(DASHBOARD_TOKEN))
    assert patients.status_code == 200
    assert patients.json()["patients"][0]["display_name"] == "Demo Patient"
    assert patients.json()["patients"][0]["bed"] == "Bed 4"
    one = api.get(f"/api/patients/{PATIENT}", headers=_auth(DASHBOARD_TOKEN))
    assert one.json()["id"] == PATIENT

    before = api.get("/api/devices", headers=_auth(DASHBOARD_TOKEN)).json()["devices"][0]
    assert before["online"] is False
    beat = api.post(f"/api/devices/{DEVICE}/heartbeat", headers=_auth(DEVICE_TOKEN))
    assert beat.status_code == 200
    assert beat.json()["online"] is True
    assert beat.json()["name"] == "bedside-demo"

    empty = api.get(
        f"/api/devices/{DEVICE}/sensor-readings/latest",
        headers=_auth(DASHBOARD_TOKEN),
    )
    assert empty.json() == {"reading": None}
    reading = api.post(
        "/api/sensor-readings",
        headers=_auth(DEVICE_TOKEN),
        json={
            "device_id": DEVICE,
            "temperature_c": 27.5,
            "humidity_pct": None,
            "observed_at": "2026-11-21T15:30:00Z",
        },
    )
    assert reading.status_code == 201
    assert reading.json()["temperature_c"] == 27.5
    latest = api.get(
        f"/api/devices/{DEVICE}/sensor-readings/latest",
        headers=_auth(DASHBOARD_TOKEN),
    )
    assert latest.json()["reading"]["id"] == reading.json()["id"]
    assert api.post(
        "/api/sensor-readings",
        headers=_auth(DEVICE_TOKEN),
        json={
            "device_id": DEVICE,
            "temperature_c": None,
            "humidity_pct": None,
            "observed_at": "2026-11-21T15:30:00Z",
        },
    ).status_code == 422
    assert api.post(
        "/api/sensor-readings",
        headers=_auth(DEVICE_TOKEN),
        json={
            "device_id": DEVICE,
            "temperature_c": 200,
            "humidity_pct": 40,
            "observed_at": "2026-11-21T15:30:00Z",
        },
    ).status_code == 422
    assert api.post(
        "/api/sensor-readings",
        headers=_auth(DEVICE_TOKEN),
        json={
            "device_id": str(uuid4()),
            "temperature_c": 21,
            "humidity_pct": 40,
            "observed_at": "2026-11-21T15:30:00Z",
        },
    ).status_code == 404

    settings = api.get("/api/settings", headers=_auth(DEVICE_TOKEN))
    assert settings.status_code == 200
    assert settings.json() == {
        "heartbeat_interval_s": 5,
        "offline_after_s": 15,
        "escalate_after_s": 60,
    }


def test_same_uuid_inserted_concurrently_is_stored_once(api):
    payload = _body()
    body = RequestIn.model_validate(payload)
    pool = api.app.state.pool

    def attempt():
        with pool.connection() as conn:
            _row, created = store.save_request(conn, body)
            return created

    with ThreadPoolExecutor(max_workers=2) as pool_exec:
        created_flags = list(pool_exec.map(lambda _n: attempt(), (1, 2)))
    assert sorted(created_flags) == [False, True]
    listed = api.get("/api/requests", headers=_auth(DASHBOARD_TOKEN)).json()["requests"]
    assert len([row for row in listed if row["id"] == payload["id"]]) == 1
