-- Central store for Read the Room.
--
-- The bedside app keeps data/alerts.json and is unchanged by this migration.
-- FastAPI, when it is added, is the only client. It connects with DATABASE_URL
-- as the database owner.
--
-- Staff login is intentionally absent. The hackathon uses fictional patients.
-- RLS is enabled and public, anon, authenticated, and service_role have no
-- privileges, so the Supabase Data API cannot read or write these tables.
-- Nurse accounts, audited acknowledgements, and row policies are a later change.
-- The nurse dashboard polls FastAPI. It does not subscribe to these tables.

create type public.request_state as enum ('pending', 'escalated', 'acked');

create table public.patients (
  id uuid primary key default gen_random_uuid(),
  display_name text not null,
  bed text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  constraint patients_display_name_present check (length(btrim(display_name)) > 0),
  constraint patients_bed_present check (length(btrim(bed)) > 0)
);

create table public.devices (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.patients (id) on delete restrict,
  name text not null,
  last_heartbeat_at timestamptz,
  created_at timestamptz not null default now(),
  constraint devices_name_present check (length(btrim(name)) > 0)
);

create index devices_patient_idx on public.devices (patient_id);
create index devices_heartbeat_idx on public.devices (last_heartbeat_at);

-- A bedside assignment is fixed for the hackathon. Reassignment history is later.
create or replace function public.devices_patient_fixed()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.patient_id is distinct from old.patient_id then
    raise exception 'device patient assignment is fixed'
      using errcode = '23514';
  end if;
  return new;
end;
$$;

create trigger devices_patient_fixed
  before update of patient_id on public.devices
  for each row
  execute function public.devices_patient_fixed();

create table public.requests (
  -- Bedside-generated and persisted before the first send. No default: a retry
  -- must reuse this UUID, and the server must not mint a second one.
  id uuid primary key,
  -- AlertStore integer id. Not unique. A missing or unreadable alerts.json
  -- starts the bedside counter at 1 again.
  local_id integer not null,
  patient_id uuid not null references public.patients (id) on delete restrict,
  device_id uuid not null references public.devices (id) on delete restrict,
  message text not null,
  urgent boolean not null,
  state public.request_state not null,
  created_at timestamptz not null,
  received_at timestamptz not null default now(),
  acked_at timestamptz,
  category text,
  detected_object text,
  context jsonb not null default '{}'::jsonb,
  constraint requests_local_id_positive check (local_id > 0),
  constraint requests_message_present check (length(btrim(message)) > 0),
  constraint requests_context_object check (jsonb_typeof(context) = 'object'),
  -- Urgent bedside alerts are created as escalated, never pending.
  constraint requests_urgent_not_pending check (not urgent or state <> 'pending'),
  constraint requests_ack_consistent check (
    (state = 'acked' and acked_at is not null)
    or (state <> 'acked' and acked_at is null)
  )
);

create index requests_state_created_idx on public.requests (state, created_at desc);
create index requests_patient_created_idx on public.requests (patient_id, created_at desc);
create index requests_device_created_idx on public.requests (device_id, created_at desc);
create index requests_device_local_idx on public.requests (device_id, local_id);

-- The request patient must be the patient that device is assigned to.
create or replace function public.requests_match_device_patient()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if not exists (
    select 1
    from public.devices as d
    where d.id = new.device_id
      and d.patient_id = new.patient_id
  ) then
    raise exception 'device % is not assigned to patient %', new.device_id, new.patient_id
      using errcode = '23514';
  end if;
  return new;
end;
$$;

create trigger requests_match_device_patient
  before insert or update of device_id, patient_id
  on public.requests
  for each row
  execute function public.requests_match_device_patient();

-- Forward only: pending -> escalated -> acked, or pending -> acked.
-- An acknowledgement and the original bedside fields stay as first written.
create or replace function public.requests_guard_update()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.id is distinct from old.id
     or new.local_id is distinct from old.local_id
     or new.patient_id is distinct from old.patient_id
     or new.device_id is distinct from old.device_id
     or new.message is distinct from old.message
     or new.urgent is distinct from old.urgent
     or new.created_at is distinct from old.created_at
     or new.received_at is distinct from old.received_at
  then
    raise exception 'request identity and original fields cannot change'
      using errcode = '23514';
  end if;

  if old.state = 'acked' and new.state is distinct from 'acked' then
    raise exception 'acknowledged request cannot return to pending or escalated'
      using errcode = '23514';
  end if;

  if old.state = 'escalated' and new.state = 'pending' then
    raise exception 'escalated request cannot return to pending'
      using errcode = '23514';
  end if;

  if old.acked_at is not null and new.acked_at is distinct from old.acked_at then
    raise exception 'acknowledgement timestamp cannot change'
      using errcode = '23514';
  end if;

  return new;
end;
$$;

create trigger requests_guard_update
  before update on public.requests
  for each row
  execute function public.requests_guard_update();

create table public.sensor_readings (
  id bigint generated always as identity primary key,
  device_id uuid not null references public.devices (id) on delete restrict,
  temperature_c double precision,
  humidity_pct double precision,
  observed_at timestamptz not null,
  constraint sensor_readings_has_value check (
    temperature_c is not null or humidity_pct is not null
  ),
  constraint sensor_readings_temperature_range check (
    temperature_c is null or temperature_c between -50 and 130
  ),
  constraint sensor_readings_humidity_range check (
    humidity_pct is null or humidity_pct between 0 and 100
  )
);

create index sensor_readings_device_observed_idx
  on public.sensor_readings (device_id, observed_at desc);

-- One row. FastAPI reads these instead of hard-coding them.
-- escalate_after_s must stay aligned with Config.escalate_after_s in the bedside app.
-- A device is offline when last_heartbeat_at is older than offline_after_s.
-- The bedside app sends the heartbeat every heartbeat_interval_s.
create table public.app_settings (
  id integer primary key,
  heartbeat_interval_s integer not null,
  offline_after_s integer not null,
  escalate_after_s integer not null,
  constraint app_settings_singleton check (id = 1),
  constraint app_settings_heartbeat_positive check (heartbeat_interval_s > 0),
  constraint app_settings_offline_after_heartbeat check (offline_after_s > heartbeat_interval_s),
  constraint app_settings_escalate_positive check (escalate_after_s > 0)
);

insert into public.app_settings (
  id, heartbeat_interval_s, offline_after_s, escalate_after_s
) values (1, 5, 15, 60);

create or replace function public.app_settings_keep_row()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  raise exception 'app_settings row cannot be deleted'
    using errcode = '23514';
end;
$$;

create trigger app_settings_keep_row
  before delete on public.app_settings
  for each row
  execute function public.app_settings_keep_row();

alter table public.patients enable row level security;
alter table public.devices enable row level security;
alter table public.requests enable row level security;
alter table public.sensor_readings enable row level security;
alter table public.app_settings enable row level security;

-- No policies. With RLS enabled, a role that later receives a grant still
-- sees no rows. service_role is included so a leaked Data API key is not a
-- second writer. FastAPI uses the database owner, which bypasses RLS.
revoke all privileges on table
  public.patients,
  public.devices,
  public.requests,
  public.sensor_readings,
  public.app_settings
from public, anon, authenticated, service_role;

revoke all privileges on sequence public.sensor_readings_id_seq
from public, anon, authenticated, service_role;

revoke all privileges on function
  public.devices_patient_fixed(),
  public.requests_match_device_patient(),
  public.requests_guard_update(),
  public.app_settings_keep_row()
from public, anon, authenticated, service_role;
