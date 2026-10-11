import os
import tempfile
import time
from pathlib import Path

import pytest

os.environ["CURADO_ALERT_CHECK_S"] = "0"
os.environ.setdefault("CURADO_DATA", tempfile.mkdtemp())

from curado.alerts import AlarmSettings, Alerter  # noqa: E402
from curado.db import Database  # noqa: E402
from curado.notify import AsteriskCall, Notifier, build_channels, spoken  # noqa: E402

T0 = 1_800_000_000


class FakeNotifier(Notifier):
    def __init__(self):
        super().__init__([])
        self.sent = []

    def send(self, subject, body, critical=False, call=False):
        self.sent.append({"subject": subject, "body": body, "critical": critical, "call": call})
        return {"fake": "ok"}


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    notifier = FakeNotifier()
    s = AlarmSettings()
    alerter = Alerter(db, notifier, lambda: s)
    return db, notifier, alerter, s


def add_readings(db, start, end, t, compressor=0, alarms=""):
    """One reading every 30 s in [start, end)."""
    for ts in range(int(start), int(end), 30):
        temp = t(ts) if callable(t) else t
        db.execute(
            "INSERT INTO readings (ts, t, rh, compressor, humidifier, fan, alarms) "
            "VALUES (?, ?, 80, ?, 0, 30, ?)", (ts, temp, compressor, alarms))


def test_temp_max_waits_for_delay_then_calls(env):
    db, notifier, alerter, s = env
    add_readings(db, T0, T0 + 600, 4.0)
    add_readings(db, T0 + 600, T0 + 900, 8.0)  # 5 min above 7 °C: door opened?
    alerter.check(T0 + 900)
    assert notifier.sent == []
    add_readings(db, T0 + 900, T0 + 1260, 8.5)  # now more than 10 min
    alerter.check(T0 + 1260)
    assert len(notifier.sent) == 1
    msg = notifier.sent[0]
    assert msg["critical"] and msg["call"]
    assert "8.5 °C" in msg["body"] and "7.0 °C" in msg["body"]


def test_critical_repeats_until_ack(env):
    db, notifier, alerter, s = env
    add_readings(db, T0, T0 + 900, 9.0)
    alerter.check(T0 + 900)
    assert len(notifier.sent) == 1
    add_readings(db, T0 + 900, T0 + 900 + 16 * 60, 9.0)
    alerter.check(T0 + 900 + 5 * 60)
    assert len(notifier.sent) == 1          # not before repeat_min
    alerter.check(T0 + 900 + 16 * 60)
    assert len(notifier.sent) == 2
    assert "(aviso 2)" in notifier.sent[1]["subject"]

    assert alerter.ack("teléfono", T0 + 2000) == 1
    assert "confirmada" in notifier.sent[2]["subject"]
    add_readings(db, T0 + 900 + 16 * 60, T0 + 900 + 60 * 60, 9.0)
    alerter.check(T0 + 900 + 60 * 60 - 1)
    assert len(notifier.sent) == 3          # acknowledged: no more calls


def test_resolution_with_hysteresis(env):
    db, notifier, alerter, s = env
    add_readings(db, T0, T0 + 900, 9.0)
    alerter.check(T0 + 900)
    add_readings(db, T0 + 900, T0 + 960, 6.8)  # below 7 but within 0.5 °C
    alerter.check(T0 + 960)
    assert alerter.open_incidents()
    add_readings(db, T0 + 960, T0 + 1020, 6.0)
    alerter.check(T0 + 1020)
    assert not alerter.open_incidents()
    assert "resuelta" in notifier.sent[-1]["subject"]
    assert not notifier.sent[-1]["call"]


def test_cooling_fail_detected_before_temp_limit(env):
    db, notifier, alerter, s = env
    # Compressor on for 50 min while the temperature creeps up: fridge dead.
    add_readings(db, T0, T0 + 3000, lambda ts: 4.0 + (ts - T0) / 3000, compressor=1)
    alerter.check(T0 + 3000)
    kinds = [i["kind"] for i in alerter.open_incidents()]
    assert kinds == ["COOLING_FAIL"]
    assert notifier.sent[0]["call"]


def test_healthy_cooling_is_not_an_alarm(env):
    db, notifier, alerter, s = env
    add_readings(db, T0, T0 + 3000, lambda ts: 6.0 - (ts - T0) / 1500, compressor=1)
    alerter.check(T0 + 3000)
    assert notifier.sent == []


def test_offline_and_back(env):
    db, notifier, alerter, s = env
    add_readings(db, T0, T0 + 300, 4.0)
    alerter.check(T0 + 300 + 11 * 60)
    assert [i["kind"] for i in alerter.open_incidents()] == ["OFFLINE"]
    assert notifier.sent[0]["call"]
    add_readings(db, T0 + 1000, T0 + 1060, 4.0)
    alerter.check(T0 + 1060)
    assert not alerter.open_incidents()


def test_warning_is_message_only(env):
    db, notifier, alerter, s = env
    add_readings(db, T0, T0 + 120, 4.0, alarms="RH_LOW")
    alerter.check(T0 + 120)
    assert len(notifier.sent) == 1
    assert not notifier.sent[0]["critical"] and not notifier.sent[0]["call"]
    add_readings(db, T0 + 120, T0 + 3000, 4.0, alarms="RH_LOW")
    alerter.check(T0 + 3000)  # no repeats for warnings
    assert len(notifier.sent) == 1


def test_disabled(env):
    db, notifier, alerter, s = env
    s.enabled = False
    add_readings(db, T0, T0 + 900, 12.0)
    alerter.check(T0 + 900)
    assert notifier.sent == []


def test_build_channels_from_env(tmp_path):
    env = {
        "CURADO_SMTP_HOST": "smtp.gmail.com", "CURADO_EMAIL_TO": "a@b.c, d@e.f",
        "CURADO_TELEGRAM_TOKEN": "x", "CURADO_TELEGRAM_CHAT_IDS": "1,2",
        "CURADO_CALLMEBOT": "+34600000000:123",
        "CURADO_ASTERISK_NUMBERS": "600111222,600333444",
        "CURADO_NTFY_URL": "https://ntfy.sh/tema",
    }
    names = [c.name for c in build_channels(env, tmp_path)]
    assert names == ["ntfy", "email", "telegram", "whatsapp", "llamada (línea analógica)"]
    assert build_channels({}, tmp_path) == []


def test_asterisk_call_files(tmp_path):
    spool = tmp_path / "outgoing"
    spool.mkdir()
    spoken_text = []

    def fake_tts(text, out):
        spoken_text.append(text)
        out.with_suffix(".wav").parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".wav").write_bytes(b"RIFF")
        return out.with_suffix(".wav")

    ch = AsteriskCall(["600111222", "600333444"], "PJSIP/{number}@fxo", spool,
                      tmp_path / "sounds", tts=fake_tts)
    ch.send("ALARMA", "Temperatura 8.5 °C.\n\nConfirma en el panel", True)
    files = sorted(spool.glob("*.call"))
    assert len(files) == 2
    first = files[0].read_text()
    assert "Channel: PJSIP/600111222@fxo" in first
    assert "Context: curado-alarma" in first
    assert files[1].stat().st_mtime - files[0].stat().st_mtime == 120  # one line: in turns
    assert spoken_text[0].startswith("Temperatura 8 coma 5  grados.")
    assert "panel" not in spoken_text[0]


def test_spoken():
    assert spoken("Temperatura 7.5 °C y 80 %") == "Temperatura 7 coma 5  grados y 80  por ciento"


def test_channel_payloads(monkeypatch, tmp_path):
    from curado import notify

    calls = []
    monkeypatch.setattr(notify, "_http", lambda url, data=None, headers=None, method=None:
                        calls.append((url, data, headers)) or b"{}")
    env = {
        "CURADO_NTFY_URL": "https://ntfy.sh/mi-tema",
        "CURADO_TELEGRAM_TOKEN": "TOK", "CURADO_TELEGRAM_CHAT_IDS": "42",
        "CURADO_CALLMEBOT": "+5215512345678:999",
        "CURADO_TWILIO_SID": "AC1", "CURADO_TWILIO_TOKEN": "t", "CURADO_TWILIO_FROM": "+1555",
        "CURADO_TWILIO_CALL_TO": "+5215512345678",
    }
    n = notify.Notifier(notify.build_channels(env, tmp_path))
    results = n.send("🚨 ALARMA", "Temperatura 8.5 °C.\n\nConfirma en el panel", critical=True,
                     call=False)
    assert results == {"ntfy": "ok", "telegram": "ok", "whatsapp": "ok"}  # no call
    import json
    url, data, _ = calls[0]
    assert url == "https://ntfy.sh/"
    body = json.loads(data)
    assert body["topic"] == "mi-tema" and body["priority"] == 5 and body["title"] == "🚨 ALARMA"
    assert calls[1][0] == "https://api.telegram.org/botTOK/sendMessage"
    assert "api.callmebot.com" in calls[2][0]

    calls.clear()
    n.send("x", "Temperatura 8.5 °C.\n\nConfirma en el panel", critical=True, call=True)
    twiml = [c for c in calls if "Calls.json" in c[0]][0][1].decode()
    assert "8+coma+5" in twiml and "panel" not in twiml


def test_one_failing_channel_does_not_stop_the_rest(tmp_path):
    from curado.notify import Channel

    class Broken(Channel):
        name = "roto"

        def send(self, *a):
            raise OSError("sin red")

    class Ok(Channel):
        name = "ok"
        got = []

        def send(self, *a):
            self.got.append(a)

    n = Notifier([Broken(), Ok()])
    assert n.send("s", "b") == {"roto": "error: sin red", "ok": "ok"}
