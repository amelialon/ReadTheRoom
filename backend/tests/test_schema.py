"""Constraints for the central schema. Runs against the temporary database."""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

PATIENT = "11111111-1111-4111-8111-111111111111"
DEVICE = "22222222-2222-4222-8222-222222222222"
CREATED = "2026-11-21T15:30:00Z"

TABLES = (
    "patients",
    "devices",
    "requests",
    "sensor_readings",
    "app_settings",
)


def insert_request(
    db,
    request_id,
    *,
    local_id=1,
    patient_id=PATIENT,
    device_id=DEVICE,
    message="Bed 4: patient is thirsty and wants water",
    urgent=False,
    state="pending",
    created_at=CREATED,
    acked_at=None,
    context="{}",
):
    db.execute(
        """
        insert into public.requests (
          id, local_id, patient_id, device_id, message, urgent, state,
          created_at, acked_at, context
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            request_id, local_id, patient_id, device_id, message, urgent,
            state, created_at, acked_at, context,
        ),
    )


def test_seed_matches_the_bedside_defaults(db):
    patient = db.execute(
        "select display_name, bed, active from public.patients where id = %s",
        (PATIENT,),
    ).fetchone()
    device = db.execute(
        "select patient_id, name, last_heartbeat_at from public.devices where id = %s",
        (DEVICE,),
    ).fetchone()
    settings = db.execute(
        """
        select heartbeat_interval_s, offline_after_s, escalate_after_s
        from public.app_settings where id = 1
        """
    ).fetchone()
    assert patient == ("Demo Patient", "Bed 4", True)
    assert str(device[0]) == PATIENT
    assert device[1:] == ("bedside-demo", None)
    assert settings == (5, 15, 60)


def test_settings_are_configurable_and_cannot_be_removed(db):
    db.execute(
        """
        update public.app_settings
        set heartbeat_interval_s = 10, offline_after_s = 30, escalate_after_s = 90
        where id = 1
        """
    )
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.app_settings set heartbeat_interval_s = 30 where id = 1"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("delete from public.app_settings where id = 1")
    finally:
        db.execute(
            """
            update public.app_settings
            set heartbeat_interval_s = 5, offline_after_s = 15, escalate_after_s = 60
            where id = 1
            """
        )


def test_data_api_roles_have_no_privileges_and_rls_is_on(db):
    locked = db.execute(
        """
        select c.relname, c.relrowsecurity
        from pg_class as c
        join pg_namespace as n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r'
        order by 1
        """
    ).fetchall()
    assert locked == [(name, True) for name in sorted(TABLES)]
    grants = db.execute(
        """
        select grantee, table_name, privilege_type
        from information_schema.role_table_grants
        where table_schema = 'public'
          and grantee in ('PUBLIC', 'anon', 'authenticated', 'service_role')
        """
    ).fetchall()
    routines = db.execute(
        """
        select grantee, routine_name
        from information_schema.routine_privileges
        where routine_schema = 'public'
          and grantee in ('PUBLIC', 'anon', 'authenticated', 'service_role')
        """
    ).fetchall()
    sequences = db.execute(
        """
        select grantee, object_name, privilege_type
        from information_schema.usage_privileges
        where object_schema = 'public'
          and object_type = 'SEQUENCE'
          and grantee in ('PUBLIC', 'anon', 'authenticated', 'service_role')
        """
    ).fetchall()
    policies = db.execute(
        "select count(*) from pg_policies where schemaname = 'public'"
    ).fetchone()[0]
    assert grants == []
    assert routines == []
    assert sequences == []
    assert policies == 0


def test_authenticated_grant_still_hides_rows(db):
    request_id = "00000000-0000-4000-8000-000000000010"
    insert_request(db, request_id)
    db.execute("grant select, insert on public.requests to authenticated")
    db.execute("set role authenticated")
    try:
        visible = db.execute("select count(*) from public.requests").fetchone()[0]
        assert visible == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            insert_request(db, "00000000-0000-4000-8000-000000000011")
    finally:
        db.execute("reset role")
        db.execute("revoke all privileges on public.requests from authenticated")
        db.execute("delete from public.requests where id = %s", (request_id,))


def test_owner_can_read_the_seed(db):
    row = db.execute(
        "select display_name from public.patients where id = %s",
        (PATIENT,),
    ).fetchone()
    assert row == ("Demo Patient",)


def test_same_local_id_can_be_reused_with_a_new_uuid(db):
    first = "00000000-0000-4000-8000-000000000020"
    second = "00000000-0000-4000-8000-000000000021"
    insert_request(db, first, local_id=1)
    insert_request(db, second, local_id=1)
    try:
        count = db.execute(
            "select count(*) from public.requests where id in (%s, %s)",
            (first, second),
        ).fetchone()[0]
        assert count == 2
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert_request(db, first, local_id=2)
    finally:
        db.execute("delete from public.requests where id in (%s, %s)", (first, second))


def test_request_must_belong_to_the_devices_patient(db):
    other = db.execute(
        """
        insert into public.patients (display_name, bed)
        values ('Other Patient', 'Bed 9')
        returning id
        """
    ).fetchone()[0]
    try:
        with pytest.raises(psycopg.errors.CheckViolation, match="not assigned"):
            insert_request(
                db,
                "00000000-0000-4000-8000-000000000030",
                patient_id=other,
            )
    finally:
        db.execute("delete from public.patients where id = %s", (other,))


def test_device_assignment_is_fixed_and_heartbeat_can_change(db):
    other = db.execute(
        """
        insert into public.patients (display_name, bed)
        values ('Other Patient', 'Bed 9')
        returning id
        """
    ).fetchone()[0]
    try:
        with pytest.raises(psycopg.errors.CheckViolation, match="assignment is fixed"):
            db.execute(
                "update public.devices set patient_id = %s where id = %s",
                (other, DEVICE),
            )
        db.execute(
            """
            update public.devices
            set last_heartbeat_at = '2026-11-21T15:00:00Z'
            where id = %s
            """,
            (DEVICE,),
        )
        seen = db.execute(
            "select last_heartbeat_at is not null from public.devices where id = %s",
            (DEVICE,),
        ).fetchone()[0]
        assert seen is True
    finally:
        db.execute(
            "update public.devices set last_heartbeat_at = null where id = %s",
            (DEVICE,),
        )
        db.execute("delete from public.patients where id = %s", (other,))


def test_pending_escalates_then_ack_is_permanent(db):
    request_id = "00000000-0000-4000-8000-000000000040"
    insert_request(db, request_id)
    try:
        db.execute(
            "update public.requests set state = 'escalated' where id = %s",
            (request_id,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="cannot return to pending"):
            db.execute(
                "update public.requests set state = 'pending' where id = %s",
                (request_id,),
            )
        db.execute(
            """
            update public.requests
            set state = 'acked', acked_at = '2026-11-21T15:32:00Z'
            where id = %s
            """,
            (request_id,),
        )
        db.execute(
            """
            update public.requests
            set state = 'acked', acked_at = '2026-11-21T15:32:00Z'
            where id = %s
            """,
            (request_id,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="cannot return"):
            db.execute(
                """
                update public.requests
                set state = 'pending', acked_at = null
                where id = %s
                """,
                (request_id,),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="cannot return"):
            db.execute(
                """
                update public.requests
                set state = 'escalated', acked_at = null
                where id = %s
                """,
                (request_id,),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="timestamp cannot change"):
            db.execute(
                """
                update public.requests
                set acked_at = '2026-11-21T15:40:00Z'
                where id = %s
                """,
                (request_id,),
            )
        row = db.execute(
            "select state, acked_at from public.requests where id = %s",
            (request_id,),
        ).fetchone()
        assert row[0] == "acked"
        assert row[1] == datetime(2026, 11, 21, 15, 32, tzinfo=timezone.utc)
    finally:
        db.execute("delete from public.requests where id = %s", (request_id,))


def test_urgent_request_starts_escalated_and_stays_urgent(db):
    request_id = "00000000-0000-4000-8000-000000000050"
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_request(
            db,
            request_id,
            message="Bed 4: patient is in PAIN",
            urgent=True,
            state="pending",
        )
    insert_request(
        db,
        request_id,
        message="Bed 4: patient is in PAIN",
        urgent=True,
        state="escalated",
    )
    try:
        with pytest.raises(psycopg.errors.CheckViolation, match="cannot change"):
            db.execute(
                "update public.requests set urgent = false where id = %s",
                (request_id,),
            )
        still = db.execute(
            "select urgent, state from public.requests where id = %s",
            (request_id,),
        ).fetchone()
        assert still == (True, "escalated")
    finally:
        db.execute("delete from public.requests where id = %s", (request_id,))


def test_original_request_fields_cannot_be_rewritten(db):
    request_id = "00000000-0000-4000-8000-000000000060"
    insert_request(db, request_id)
    try:
        with pytest.raises(psycopg.errors.CheckViolation, match="cannot change"):
            db.execute(
                "update public.requests set message = 'rewritten' where id = %s",
                (request_id,),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="cannot change"):
            db.execute(
                "update public.requests set local_id = 9 where id = %s",
                (request_id,),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="cannot change"):
            db.execute(
                "update public.requests set received_at = now() where id = %s",
                (request_id,),
            )
    finally:
        db.execute("delete from public.requests where id = %s", (request_id,))


def test_context_can_be_filled_later_and_must_be_an_object(db):
    request_id = "00000000-0000-4000-8000-000000000070"
    insert_request(db, request_id)
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "update public.requests set context = '[]'::jsonb where id = %s",
                (request_id,),
            )
        db.execute(
            """
            update public.requests
            set category = 'hydration',
                detected_object = 'water bottle',
                context = '{"temperature_c": 27.5}'::jsonb
            where id = %s
            """,
            (request_id,),
        )
        row = db.execute(
            """
            select category, detected_object, context
            from public.requests where id = %s
            """,
            (request_id,),
        ).fetchone()
        assert row[0] == "hydration"
        assert row[1] == "water bottle"
        assert row[2] == {"temperature_c": 27.5}
    finally:
        db.execute("delete from public.requests where id = %s", (request_id,))


def test_ack_timestamp_must_match_the_state(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_request(
            db,
            "00000000-0000-4000-8000-000000000080",
            state="acked",
            acked_at=None,
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_request(
            db,
            "00000000-0000-4000-8000-000000000081",
            state="pending",
            acked_at="2026-11-21T15:32:00Z",
        )


def test_resolved_is_not_a_state(db):
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        insert_request(
            db,
            "00000000-0000-4000-8000-000000000090",
            state="resolved",
        )


def test_empty_message_is_rejected(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_request(
            db,
            "00000000-0000-4000-8000-0000000000a0",
            message="   ",
        )


def test_sensor_readings_require_a_plausible_measurement(db):
    db.execute(
        """
        insert into public.sensor_readings
          (device_id, temperature_c, humidity_pct, observed_at)
        values (%s, 27.5, null, '2026-11-21T15:30:00Z')
        """,
        (DEVICE,),
    )
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """
                insert into public.sensor_readings
                  (device_id, temperature_c, humidity_pct, observed_at)
                values (%s, null, null, '2026-11-21T15:30:00Z')
                """,
                (DEVICE,),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """
                insert into public.sensor_readings
                  (device_id, temperature_c, humidity_pct, observed_at)
                values (%s, 200, 40, '2026-11-21T15:30:00Z')
                """,
                (DEVICE,),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """
                insert into public.sensor_readings
                  (device_id, temperature_c, humidity_pct, observed_at)
                values (%s, 22, 140, '2026-11-21T15:30:00Z')
                """,
                (DEVICE,),
            )
    finally:
        db.execute("delete from public.sensor_readings where device_id = %s", (DEVICE,))
