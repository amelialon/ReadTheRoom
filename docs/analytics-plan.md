# Analytics plan: what the graphs should say

The analytics page today shows four numbers and a bar per request type, all computed from the last 30 requests the device keeps. This plan replaces it with graphs that answer the questions a charge nurse, a family member and a judge would actually ask, using data the system already records.

## The questions, and the graph that answers each

| Question | Graph | Data it needs |
| --- | --- | --- |
| When does this patient need help? | **Day strip**: 24 bars, one per hour, requests stacked by type; urgent ones in red. A faint second strip underneath for yesterday. | `requests.created_at`, `urgent`, message type |
| Are we getting to the patient fast enough? | **Time to a nurse**: a dot per request on a 7-day axis, with a rolling median line and the 60 s escalation target as a hairline. Dots above the line are the ones to talk about. | `created_at`, `acked_at`, `app_settings.escalate_after_s` |
| What does the patient ask for most, and is it changing? | **Needs over the week**: small horizontal bars per type (as now), each with a 7-day sparkline beside it. | message type by day |
| Is the room comfortable when the patient says it isn't? | **Room comfort line**: temperature over 24 h with the comfortable band (19 to 25 °C) shaded sage, and a blush marker at every blanket or "too warm" request. This is the climate pitch in one picture. | `sensor_readings.temperature_c`, blanket/climate requests |
| Can the device see the patient? | **Attention band**: a thin timeline where the face was in view (sage), not in view (grey) and the device was offline (red). Explains any gap in requests. | face_ok and heartbeat samples |
| How is the whole ward doing? (staff only) | **Ward heatmap**: beds down the side, hours across, cell colour by open-request minutes. | all beds' requests |

Rules for every graph: one accent (sage), red only for urgent and offline, blush for anything on its way to a nurse, tabular numerals, no gridlines heavier than a hairline, no legends where a label on the mark will do. Each graph has an empty state in words ("No requests yesterday").

## Where the data comes from

- **Now**: the device keeps the last 30 answered requests plus every open one. Enough for the day strip and time-to-nurse for one shift, not for a week.
- **Next**: the teammate's FastAPI has `GET /requests?limit=200` with `created_at`, `acked_at`, `urgent`, `message` and the bed, and `POST /sensors` for readings. Add two reads:
  - `GET /requests?since=<iso>&patient_id=` so the page can ask for seven days.
  - `GET /sensors?device_id=&since=` returning temperature and humidity samples.
- **Needs a new record**: the attention band. Log a face_ok sample every 5 s alongside the heartbeat (one boolean per sample, trivially small) and the prompts asked with their outcome (yes, no, timed out), so "asked 14 times, answered 12" becomes a number.

## How to draw them

No chart library. Each graph is an inline SVG built by a 30 to 60 line function in the page: bars are `rect`s, lines are one `path`, the comfort band is one `rect` behind the line. This keeps the page offline-capable, matches the glass look exactly, and transitions can use `transform: scaleY` on bars (compositor only). If the ward heatmap grows past a few beds, switch that one graph to a `canvas`.

Every graph takes the same shape of input, `[{t, value, kind}]`, so the same function draws the day strip for requests and the comfort line for temperature.

## Build order and effort

1. **Day strip and time-to-nurse from device data** (about 2 h). Works today with no backend. Replace the four stat tiles with these two graphs; keep the "what was asked for" bars.
2. **Room comfort line** (about 1.5 h). Needs sensor history: either the backend's `GET /sensors` or, for the demo, a ring buffer of the last 24 h of readings kept in memory on the device and published in the state.
3. **Seven-day range through FastAPI** (about 1.5 h, needs the backend running). Add `since` to the two list routes and point the page at it when `?api=` is set.
4. **Attention band and prompt outcomes** (about 2 h). New logging on the device, then one more strip on the page.
5. **Ward heatmap** (later). Only meaningful with more than one device.

For the demo, step 1 plus a seeded comfort line (step 2 with the in-memory buffer) is the strongest return: it shows the climate idea and the escalation target in two pictures, with no network dependency.

## What to leave out

Pie charts (two request types make a bad pie), percentages against a made-up baseline, averages hiding the long waits (use the median and show the dots), and anything that needs more than one glance to read at a nurse station.
