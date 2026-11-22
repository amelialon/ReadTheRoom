"""The device's line to the central API, against a fake server."""
import time

from readtheroom.alerts import AlertStore
from readtheroom.central import CentralReporter

DEV, PAT = "22222222-2222-4222-8222-222222222222", "11111111-1111-4111-8111-111111111111"


class FakeServer:
    """Answers like backend/ does; remembers every call."""
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.down = False

    def handle(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.down:
            return 0, {}
        if path.endswith("/heartbeat"):
            return 200, {"id": DEV, "online": True}
        if path == "/api/requests" and method == "POST":
            if body["urgent"] and body["state"] == "pending":
                return 422, {"detail": "urgent requests start escalated"}
            row = self.rows.get(body["id"])
            if row is None:
                self.rows[body["id"]] = dict(body)
                return 201, dict(body)
            if body["state"] == "escalated" and row["state"] == "pending":
                row["state"] = "escalated"
            return 200, dict(row)
        if path.startswith("/api/requests?"):
            acked = [r for r in self.rows.values() if r["state"] == "acked"]
            return 200, {"requests": acked}
        if path == "/api/sensor-readings":
            return 201, dict(body, id=1)
        return 404, {}

    def ack(self, rid):
        self.rows[rid]["state"] = "acked"
        self.rows[rid]["acked_at"] = "2026-11-22T10:00:00Z"


def make(tmp_path, server, temp=None):
    alerts = AlertStore(60.0, str(tmp_path / "alerts.json"))
    rep = CentralReporter(alerts, "https://central.example", "device-token", DEV, PAT,
                          path=str(tmp_path / "central.json"), temperature=temp)
    rep.request = server.handle
    return alerts, rep


def test_requests_go_up_once_with_a_stable_uuid(tmp_path):
    server = FakeServer()
    alerts, rep = make(tmp_path, server)
    a = alerts.create("Bed 4: patient is thirsty and wants water")
    rep.sync(0.0)
    posts = [c for c in server.calls if c[0] == "POST" and c[1] == "/api/requests"]
    assert len(posts) == 1
    body = posts[0][2]
    assert body["local_id"] == a["id"] and body["state"] == "pending" and body["patient_id"] == PAT
    assert body["created_at"].endswith("Z")
    rep.sync(1.0)
    assert len([c for c in server.calls if c[1] == "/api/requests" and c[0] == "POST"]) == 1  # nothing new
    assert rep.online

    # A restart keeps the same uuid: the same request is not sent as a new one.
    _, again = make(tmp_path, server)
    again.alerts = alerts
    again.sync(0.0)
    assert len(server.rows) == 1 and again.uuid_for(a) == body["id"]


def test_escalation_is_posted_and_dashboard_acks_come_back(tmp_path):
    server = FakeServer()
    alerts, rep = make(tmp_path, server)
    a = alerts.create("Bed 4: patient needs the toilet")
    rep.sync(0.0)
    alerts._deadline[a["id"]] = 0  # due now
    alerts.tick()
    assert alerts.list()[0]["state"] == "escalated"
    rep.sync(1.0)
    rid = rep.uuid_for(a)
    assert server.rows[rid]["state"] == "escalated"

    server.ack(rid)
    rep.sync(2.0)
    assert alerts.list()[0]["state"] == "acked" and alerts.list()[0]["acked_at"] is not None


def test_no_network_is_retried_not_raised(tmp_path):
    server = FakeServer()
    server.down = True
    alerts, rep = make(tmp_path, server)
    alerts.create("Bed 4: patient is in pain", urgent=True)
    rep.sync(0.0)
    assert not rep.online and not server.rows
    server.down = False
    rep.sync(10.0)
    assert rep.online and len(server.rows) == 1
    assert list(server.rows.values())[0]["state"] == "escalated"  # urgent starts escalated


def test_temperature_goes_up_once_a_minute(tmp_path):
    server = FakeServer()
    alerts, rep = make(tmp_path, server, temp=lambda: 22.5)
    rep.sync(0.0)
    rep.sync(1.0)
    readings = [c for c in server.calls if c[1] == "/api/sensor-readings"]
    assert len(readings) == 1 and readings[0][2]["temperature_c"] == 22.5
    rep.sync(61.0)
    assert len([c for c in server.calls if c[1] == "/api/sensor-readings"]) == 2


def test_thread_runs_and_stops(tmp_path):
    server = FakeServer()
    alerts, rep = make(tmp_path, server)
    rep.poll_s = 0.01
    rep.start()
    time.sleep(0.1)
    rep.stop()
    rep.join(1.0)
    assert not rep.is_alive() and any(c[1].endswith("/heartbeat") for c in server.calls)
