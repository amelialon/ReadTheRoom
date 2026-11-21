# Central database

PostgreSQL schema for patients, devices, requests, sensor readings, and the shared timing settings. Supabase hosts the database. FastAPI will be the only client. This directory does not change the bedside app.

The schema is in `migrations/20261121100000_central_schema.sql`. Fictional demo rows are in `seed.sql`.

Hosted project: `read-the-room` (`vdntgrysziohpbahmlzw`). Apply the SQL in the Supabase SQL Editor. Do not point the local tests at that database.

## Decisions that are already in the schema

- Request states are `pending`, `escalated`, and `acked`. There is no resolved state.
- `requests.id` is the UUID the bedside device generates and saves before it retries. The database does not generate it.
- `local_id` is the integer from `AlertStore`. It is not unique: a missing or unreadable `alerts.json` starts that counter at 1 again.
- An update cannot move `acked` back to `pending` or `escalated`, cannot move `escalated` back to `pending`, and cannot change the acknowledgement time or the original message, urgency, or bedside clock.
- A device's patient assignment is fixed. Heartbeat time can still change.
- `category`, `detected_object`, and `context` may stay empty. The gaze and detection code is not required to fill them.
- `app_settings` holds the timing values: heartbeat every 5 seconds, offline after 15 seconds, escalation after 60 seconds. Change them with `update public.app_settings`. The bedside `Config.escalate_after_s` must stay on the same 60 second value.
- The nurse dashboard will poll FastAPI every 1–2 seconds. That interval is a client choice, not a database setting.

## Authentication, and what comes later

This prototype has no nurse registration, login, or role table. The patients are fictional.

RLS is enabled on every table and there are no policies. Privileges are revoked from `public`, `anon`, `authenticated`, and `service_role`. The Supabase Data API therefore cannot read or write these tables, including with the service-role key. FastAPI will connect as the database owner through `DATABASE_URL`, which bypasses RLS.

A later version should add real staff authentication: individual nurse accounts, a record of who acknowledged a request, and policies that match those accounts. Do not put the service-role key or the database password in the dashboard.

## Apply on Supabase

Do this once. Do not commit the database password.

1. Open the SQL Editor for project `read-the-room`: https://supabase.com/dashboard/project/vdntgrysziohpbahmlzw/sql/new
2. Paste the full contents of `migrations/20261121100000_central_schema.sql` and run it.
3. Paste the full contents of `seed.sql` and run it.

If the editor offers to expose the new tables through the Data API, leave that off.

## Verify in the SQL editor

```sql
select c.relname, c.relrowsecurity
from pg_class as c
join pg_namespace as n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname in (
    'patients', 'devices', 'requests', 'sensor_readings', 'app_settings'
  )
order by 1;

select grantee, table_name, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and grantee in ('PUBLIC', 'anon', 'authenticated', 'service_role');

select * from public.app_settings;
select id, display_name, bed from public.patients;
select id, patient_id, name from public.devices;
```

Expect five tables with `relrowsecurity` true, no grants for those roles, settings `5 / 15 / 60`, patient `Demo Patient` in `Bed 4`, and device `bedside-demo`.

This transaction checks the lifecycle rules and then throws the rows away:

```sql
begin;

insert into public.requests (
  id, local_id, patient_id, device_id, message, urgent, state, created_at
) values (
  '550e8400-e29b-41d4-a716-446655440000',
  1,
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  'Bed 4: patient is thirsty and wants water',
  false,
  'pending',
  '2026-11-21T15:30:00Z'
);

-- Same bedside integer, different request: allowed.
insert into public.requests (
  id, local_id, patient_id, device_id, message, urgent, state, created_at
) values (
  '550e8400-e29b-41d4-a716-446655440001',
  1,
  '11111111-1111-4111-8111-111111111111',
  '22222222-2222-4222-8222-222222222222',
  'Bed 4: patient is thirsty and wants water',
  false,
  'pending',
  '2026-11-21T15:31:00Z'
);

update public.requests
set state = 'escalated'
where id = '550e8400-e29b-41d4-a716-446655440000';

update public.requests
set state = 'acked', acked_at = '2026-11-21T15:32:00Z'
where id = '550e8400-e29b-41d4-a716-446655440000';

-- This update must fail, which aborts the transaction.
update public.requests
set state = 'pending', acked_at = null
where id = '550e8400-e29b-41d4-a716-446655440000';

rollback;
```

The last update should report that an acknowledged request cannot return to pending or escalated. Run `rollback` if the editor leaves the transaction open. `select count(*) from public.requests` should then be `0`.

## Test the schema locally

The tests start a temporary Postgres on this machine. They do not read `DATABASE_URL` and they do not connect to Supabase.

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd backend && .venv/bin/pytest
```

From the repository root, `python3 -m pytest` stays on the bedside suite.

## Credentials

`backend/.env.example` shows the variable FastAPI and Railway will use later. Copy it to `backend/.env` only when the API work starts, and keep the real password out of git. `.env` is gitignored.
