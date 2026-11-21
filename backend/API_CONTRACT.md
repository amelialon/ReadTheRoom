# Read the Room API contract

Base URL locally: `http://localhost:8001`

Interactive docs: `http://localhost:8000` is the bedside app. This API is `http://localhost:8001/docs`.

The dashboard polls `GET /api/requests` every 1 second. That is the live update path. Repeat a failed poll; do not keep a socket open.

## Authentication

There is no nurse login. Two shared bearer tokens separate the device from the dashboard. Send exactly one:

```http
Authorization: Bearer <token>
```

| Token | May call |
|---|---|
| `DEVICE_API_TOKEN` | `POST /api/requests`, `GET /api/requests`, `GET /api/requests/{id}`, `POST /api/devices/{id}/heartbeat`, `POST /api/sensor-readings`, `GET /api/settings` |
| `DASHBOARD_API_TOKEN` | `GET /api/requests`, `GET /api/requests/{id}`, `PATCH /api/requests/{id}`, `GET /api/patients`, `GET /api/patients/{id}`, `GET /api/devices`, `GET /api/devices/{id}/sensor-readings/latest`, `GET /api/settings` |

`GET /health` has no token. Its body is only `status` and `database`.

| Status | When |
|---|---|
| 401 | Missing or unknown token |
| 403 | Known token used on the other role's endpoint, or the device is not assigned to that patient |
| 404 | Unknown patient, device, or request |
| 409 | Same request UUID with different original details |
| 422 | Body does not match the rules below |
| 201 | First time this request UUID is stored |
| 200 | Read, acknowledgement, heartbeat, or a retry of an existing UUID |

The database password and both tokens stay on the server. The React app receives only `DASHBOARD_API_TOKEN` from its own environment, never `DATABASE_URL`.

## States

`pending`, `escalated`, and `acked`. There is no resolved state.

Urgent bedside requests are created as `escalated`. A normal request is created as `pending` and later posted again as `escalated`. The dashboard does not escalate. It acknowledges.

`overdue` is true when the request is still open and either it is `escalated` and not urgent, or `created_at` is at least `escalate_after_s` old (60 seconds in the seed). `waiting_s` is computed by the server.

An acknowledged request stays acknowledged. A second acknowledgement returns the original `acked_at`.

## POST /api/requests

Device token. The device generates `id` and sends that same UUID on every retry.

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "local_id": 1,
  "patient_id": "11111111-1111-4111-8111-111111111111",
  "device_id": "22222222-2222-4222-8222-222222222222",
  "message": "Bed 4: patient is thirsty and wants water",
  "urgent": false,
  "state": "pending",
  "created_at": "2026-11-21T15:30:00Z",
  "acked_at": null,
  "category": null,
  "detected_object": null,
  "context": {}
}
```

`local_id` is the bedside integer. It may repeat after a local reset, so it is not unique. `created_at` is the bedside clock and must include a timezone. `category`, `detected_object`, and `context` may be empty.

The first successful POST returns `201`. An identical retry returns `200` and the stored row, including the original `acked_at` if a nurse has already acknowledged it. Posting `state: "escalated"` for the same UUID moves `pending` to `escalated`. Posting `state: "acked"` is accepted only on the first insert (the bedside already acknowledged it before the network returned). It does not acknowledge a row that is already stored; that is `PATCH`.

`urgent: true` with `state: "pending"` is `422`.

## GET /api/requests

Device or dashboard token. Query parameters: `state`, `patient_id`, `device_id`, `limit` (default 100, maximum 200). Newest `created_at` first.

```json
{
  "requests": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "local_id": 1,
      "patient_id": "11111111-1111-4111-8111-111111111111",
      "device_id": "22222222-2222-4222-8222-222222222222",
      "display_name": "Demo Patient",
      "bed": "Bed 4",
      "message": "Bed 4: patient is thirsty and wants water",
      "urgent": false,
      "state": "pending",
      "created_at": "2026-11-21T15:30:00Z",
      "received_at": "2026-11-21T15:30:02Z",
      "acked_at": null,
      "category": null,
      "detected_object": null,
      "context": {},
      "waiting_s": 4,
      "overdue": false
    }
  ]
}
```

`POST`, `GET /api/requests/{id}`, and `PATCH` all return this same object, not the list wrapper.

Poll this list every 1 second with the dashboard token. Use `id` as the React key. Stop the timer when the page is hidden if you want; on return, fetch immediately and then continue every 1 second.

## GET /api/requests/{id}

Device or dashboard token. `404` when the UUID is unknown. Same object as one list item.

## PATCH /api/requests/{id}

Dashboard token only.

```json
{ "state": "acked" }
```

The first call sets `acked_at` to the server clock and returns `200`. A repeat returns `200` with that same `acked_at`. The body cannot send any other state.

## GET /api/patients and GET /api/patients/{id}

Dashboard token.

```json
{
  "patients": [
    {
      "id": "11111111-1111-4111-8111-111111111111",
      "display_name": "Demo Patient",
      "bed": "Bed 4",
      "active": true,
      "created_at": "2026-11-21T15:00:00Z"
    }
  ]
}
```

`GET /api/patients/{id}` returns the object without the `patients` wrapper. Unknown id is `404`.

## GET /api/devices

Dashboard token. `online` is true when `last_heartbeat_at` is within `offline_after_s` (15 seconds in the seed).

```json
{
  "devices": [
    {
      "id": "22222222-2222-4222-8222-222222222222",
      "patient_id": "11111111-1111-4111-8111-111111111111",
      "name": "bedside-demo",
      "display_name": "Demo Patient",
      "bed": "Bed 4",
      "last_heartbeat_at": null,
      "online": false,
      "created_at": "2026-11-21T15:00:00Z"
    }
  ]
}
```

## POST /api/devices/{id}/heartbeat

Device token. No body. Sets `last_heartbeat_at` to the server clock and returns the device object above. Unknown device is `404`. Send one about every `heartbeat_interval_s` (5 seconds). The dashboard treats the device as offline after `offline_after_s` (15 seconds) without one.

## POST /api/sensor-readings

Device token.

```json
{
  "device_id": "22222222-2222-4222-8222-222222222222",
  "temperature_c": 27.5,
  "humidity_pct": 40,
  "observed_at": "2026-11-21T15:30:00Z"
}
```

One of `temperature_c` or `humidity_pct` may be null, not both. Temperature must be from -50 to 130. Humidity must be from 0 to 100. `observed_at` must include a timezone. Returns `201` and the stored reading, including its numeric `id`.

## GET /api/devices/{id}/sensor-readings/latest

Dashboard token.

```json
{ "reading": null }
```

or

```json
{
  "reading": {
    "id": 1,
    "device_id": "22222222-2222-4222-8222-222222222222",
    "temperature_c": 27.5,
    "humidity_pct": 40,
    "observed_at": "2026-11-21T15:30:00Z"
  }
}
```

Unknown device is `404`. A known device with no readings returns `"reading": null`.

## GET /api/settings

Device or dashboard token. Read only.

```json
{
  "heartbeat_interval_s": 5,
  "offline_after_s": 15,
  "escalate_after_s": 60
}
```

The dashboard can trust `overdue` on each request and does not have to recompute it. These values are the ones the server used.

## Fictional seed

| | |
|---|---|
| Patient | `11111111-1111-4111-8111-111111111111`, Demo Patient, Bed 4 |
| Device | `22222222-2222-4222-8222-222222222222`, bedside-demo |
