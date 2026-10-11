import os
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

os.environ["CURADO_ALERT_CHECK_S"] = "0"
os.environ.setdefault("CURADO_DATA", tempfile.mkdtemp())  # for the module-level app

from fastapi.testclient import TestClient  # noqa: E402

from curado import programs, settings  # noqa: E402
from curado.app import create_app  # noqa: E402
from curado.notify import Notifier  # noqa: E402

PROGRAMS_DIR = Path(__file__).resolve().parent.parent / "programs"
TOKEN = {"X-Device-Token": settings.DEVICE_TOKEN}


class FakeNotifier(Notifier):
    def __init__(self):
        super().__init__([])
        self.sent = []

    def send(self, subject, body, critical=False, call=False):
        self.sent.append({"subject": subject, "body": body, "critical": critical, "call": call})
        return {"fake": "ok"}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, PROGRAMS_DIR, notifier=FakeNotifier())
    with TestClient(app) as c:
        c.app_state = app.state
        yield c


def telemetry(t=14.0, rh=75.0, alarms=(), compressor=False, cfg_version=0):
    return {
        "device": "test",
        "uptime": 10,
        "rssi": -60,
        "cfg_version": cfg_version,
        "t": t,
        "rh": rh,
        "sensors": [
            {"name": "arriba", "ok": True, "t": t + 0.5, "rh": rh - 1},
            {"name": "medio", "ok": True, "t": t, "rh": rh},
            {"name": "abajo", "ok": False},
        ],
        "outputs": {"compressor": compressor, "humidifier": False, "fan": 30},
        "alarms": list(alarms),
    }


def test_telemetry_requires_token(client):
    assert client.post("/api/telemetry", json=telemetry()).status_code == 401


def test_telemetry_returns_config_and_is_stored(client):
    r = client.post("/api/telemetry", json=telemetry(compressor=True), headers=TOKEN)
    assert r.status_code == 200
    body = r.json()
    assert body["config"]["temp_c"] == 4
    assert body["version"] > 0

    status = client.get("/api/status").json()
    assert status["online"]
    assert status["reading"]["s0_t"] == 14.5
    assert status["reading"]["s2_t"] is None
    assert status["duty_24h"]["compressor"] == 100
    assert not status["config_synced"]

    client.post("/api/telemetry", json=telemetry(cfg_version=body["version"]), headers=TOKEN)
    assert client.get("/api/status").json()["config_synced"]


def test_settings_change_version_and_validation(client):
    v1 = client.post("/api/telemetry", json=telemetry(), headers=TOKEN).json()["version"]
    s = client.get("/api/settings").json()
    s["setpoints"]["temp_c"] = 12.5
    r = client.put("/api/settings", json=s)
    assert r.status_code == 200
    resp = client.post("/api/telemetry", json=telemetry(), headers=TOKEN).json()
    assert resp["version"] != v1
    assert resp["config"]["temp_c"] == 12.5

    s["setpoints"]["rh"] = 120
    assert client.put("/api/settings", json=s).status_code == 422
    s["setpoints"]["rh"] = 75
    s["setpoints"]["photo_interval"] = 10
    assert client.put("/api/settings", json=s).status_code == 422


def test_program_mode_overrides_setpoints(client):
    start = date.today() - timedelta(days=20)
    s = {"mode": "program", "program": "jamon_serrano", "program_start": start.isoformat()}
    r = client.put("/api/settings", json=s)
    assert r.status_code == 200, r.text
    state = r.json()["program"]
    assert state["phase_name"].startswith("Post-salado")
    assert r.json()["config"]["temp_c"] == 4

    s["program"] = "no_existe"
    assert client.put("/api/settings", json=s).status_code == 400


def test_program_ramp():
    program = programs.load_programs(PROGRAMS_DIR)["jamon_serrano"]
    # Secado starts on day 55 and ramps from 4 C / 82 % to 12 C / 76 % in 21 days.
    assert programs.evaluate(program, 55).setpoints["temp_c"] == 4
    mid = programs.evaluate(program, 55 + 10.5).setpoints
    assert mid["temp_c"] == 8
    assert mid["rh"] == 79
    assert programs.evaluate(program, 80).setpoints["temp_c"] == 12
    end = programs.evaluate(program, 10_000)
    assert end.finished and end.phase_name.startswith("Maduración")


def test_photo_upload_and_listing(client, tmp_path):
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    assert client.post("/api/photo", content=jpeg, headers=TOKEN).status_code == 200
    assert client.post("/api/photo", content=b"nope", headers=TOKEN).status_code == 400
    photos = client.get("/api/photos").json()
    assert len(photos) == 1
    assert client.get(f"/photos/{photos[0]['path']}").content == jpeg


def test_hams_weight_loss(client):
    ham = client.post("/api/hams", json={"name": "Jamón 1", "initial_weight_g": 10000}).json()
    assert ham["loss_pct"] == 0
    ham = client.post(f"/api/hams/{ham['id']}/weights", json={"weight_g": 8000}).json()
    assert ham["loss_pct"] == 20
    assert ham["target_weight_g"] == 6700
    assert not ham["done"]
    ham = client.post(f"/api/hams/{ham['id']}/weights", json={"weight_g": 6600}).json()
    assert ham["done"]
    assert client.post("/api/hams/999/weights", json={"weight_g": 1}).status_code == 404


def test_history_buckets(client):
    for _ in range(3):
        client.post("/api/telemetry", json=telemetry(t=14), headers=TOKEN)
    h = client.get("/api/history?hours=1").json()
    assert len(h["rows"]) >= 1
    assert h["rows"][0]["t"] == 14


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Cámara de curado" in r.text


def test_cold_program_holds_4c():
    program = programs.load_programs(PROGRAMS_DIR)["jamon_4c"]
    for day in (0, 30, 80, 200, 1000):
        assert programs.evaluate(program, day).setpoints["temp_c"] == 4
