"""Alarm engine.

Every minute it evaluates the rules below against the telemetry. A rule that
becomes true opens an *incident*:

  critical  -> message on every channel + phone call, repeated every
               repeat_min minutes until someone acknowledges it (dashboard
               button or pressing 1 during the call). When it clears, a
               "resolved" message is sent.
  warning   -> one message, no call.

Critical rules:
  TEMP_MAX      temperature above temp_max (7 °C) for delay_min minutes
  TEMP_MIN      temperature below temp_min (freezing) for delay_min minutes
  COOLING_FAIL  compressor relay on for cooling_fail_min minutes and the
                temperature is not dropping: fridge unplugged, broken, tripped
                breaker, open door... it fires before the temperature limit
  OFFLINE       no telemetry from the ESP32 (power cut, WiFi down)
  SENSOR_FAIL   no sensor answers (the ESP32 turns the fridge off)

Incidents live in the database, so a restart neither forgets nor repeats them.
"""

import logging
import threading
import time
import urllib.request
from typing import Callable

from pydantic import BaseModel, Field, model_validator

from . import settings
from .db import Database
from .notify import Notifier

log = logging.getLogger("curado.alerts")

READING_PERIOD_S = 30  # ESP32 telemetry period

DEVICE_WARNINGS = {
    "SENSOR_PARTIAL": "Algún sensor no responde",
    "TEMP_HIGH": "Temperatura por encima de la consigna",
    "TEMP_LOW": "Temperatura por debajo de la consigna",
    "RH_HIGH": "Humedad demasiado alta",
    "RH_LOW": "Humedad demasiado baja",
}


class AlarmSettings(BaseModel):
    enabled: bool = True
    temp_max: float = Field(7, ge=-5, le=40)
    temp_min: float = Field(0, ge=-10, le=30)
    delay_min: float = Field(10, ge=0, le=240)
    cooling_fail_min: float = Field(45, ge=0, le=600)  # 0 disables the rule
    repeat_min: float = Field(15, ge=2, le=1440)
    call_on_critical: bool = True

    @model_validator(mode="after")
    def check(self):
        if self.temp_min >= self.temp_max:
            raise ValueError("temp_min debe ser menor que temp_max")
        return self


def _fmt(v: float) -> str:
    return f"{v:.1f}"


class Alerter:
    def __init__(self, db: Database, notifier: Notifier,
                 get_settings: Callable[[], AlarmSettings]):
        self.db = db
        self.notifier = notifier
        self.get_settings = get_settings
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_warning: dict[str, float] = {}

    # --- rules ---------------------------------------------------------------

    def conditions(self, now: float, s: AlarmSettings,
                   open_kinds: set[str]) -> tuple[dict[str, tuple[bool, str]], bool]:
        """Active conditions {kind: (critical, message)} and whether the data
        is stale (ESP32 offline: the other rules cannot be evaluated)."""
        last = self.db.query_one("SELECT * FROM readings ORDER BY ts DESC LIMIT 1")
        if last is None:
            return {}, False

        age = now - last["ts"]
        offline_after = 120 if "OFFLINE" in open_kinds else settings.OFFLINE_ALERT_MIN * 60
        if age > offline_after:
            msg = (f"La cámara de curado no envía datos desde hace {age / 60:.0f} minutos. "
                   f"Última temperatura {_fmt(last['t']) if last['t'] is not None else 'desconocida'} °C. "
                   "Puede ser un corte de luz o del WiFi.")
            return {"OFFLINE": (True, msg)}, True

        conds: dict[str, tuple[bool, str]] = {}
        t = last["t"]
        comp = "encendida" if last["compressor"] else "apagada"

        window = self.db.query(
            "SELECT t FROM readings WHERE ts > ? AND t IS NOT NULL", (now - s.delay_min * 60,)
        )
        temps = [r["t"] for r in window]
        enough = len(temps) >= max(1, int(0.5 * s.delay_min * 60 / READING_PERIOD_S))

        if t is not None:
            if "TEMP_MAX" in open_kinds:
                hot = t > s.temp_max - 0.5  # hysteresis: clears 0.5 °C below the limit
            else:
                hot = enough and min(temps) > s.temp_max
            if hot:
                conds["TEMP_MAX"] = (True, (
                    f"Temperatura de la cámara de curado {_fmt(t)} °C, por encima del límite de "
                    f"{_fmt(s.temp_max)} °C. La nevera está {comp}."))

            if "TEMP_MIN" in open_kinds:
                cold = t < s.temp_min + 0.5
            else:
                cold = enough and max(temps) < s.temp_min
            if cold:
                conds["TEMP_MIN"] = (True, (
                    f"Temperatura de la cámara de curado {_fmt(t)} °C, por debajo de "
                    f"{_fmt(s.temp_min)} °C. Riesgo de congelación. La nevera está {comp}."))

        if s.cooling_fail_min > 0:
            fail = self._cooling_fail(now, s.cooling_fail_min)
            if fail is not None:
                conds["COOLING_FAIL"] = (True, (
                    f"La nevera lleva {s.cooling_fail_min:.0f} minutos encendida y no enfría "
                    f"(temperatura {_fmt(fail)} °C). Revisa que esté enchufada, la puerta "
                    "cerrada y el relé."))

        for alarm in filter(None, (last["alarms"] or "").split(",")):
            if alarm == "SENSOR_FAIL":
                conds[alarm] = (True, "Ningún sensor de la cámara de curado responde. "
                                      "La nevera y el humidificador se han apagado por seguridad.")
            elif alarm in DEVICE_WARNINGS:
                conds[alarm] = (False, DEVICE_WARNINGS[alarm])
        return conds, False

    def _cooling_fail(self, now: float, minutes: float) -> float | None:
        rows = self.db.query(
            "SELECT ts, t, compressor FROM readings WHERE ts > ? ORDER BY ts",
            (now - minutes * 60,),
        )
        expected = minutes * 60 / READING_PERIOD_S
        if len(rows) < 0.8 * expected or not all(r["compressor"] for r in rows):
            return None
        temps = [r["t"] for r in rows if r["t"] is not None]
        if len(temps) < 4:
            return None
        q = max(1, len(temps) // 4)
        start = sum(temps[:q]) / q
        end = sum(temps[-q:]) / q
        return end if start - end < 0.2 else None

    # --- incidents -------------------------------------------------------------

    def open_incidents(self) -> list[dict]:
        return self.db.query(
            "SELECT * FROM incidents WHERE resolved_ts IS NULL ORDER BY started_ts")

    def check(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        s = self.get_settings()
        if not s.enabled:
            return
        with self._lock:
            open_ = {i["kind"]: i for i in self.open_incidents()}
            conds, stale = self.conditions(now, s, set(open_))

            for kind, (critical, msg) in conds.items():
                inc = open_.get(kind)
                if inc is None:
                    if not critical and now - self._last_warning.get(kind, 0) < settings.ALERT_REPEAT_MIN * 60:
                        continue  # a flapping warning is not repeated every minute
                    cur = self.db.execute(
                        "INSERT INTO incidents (kind, critical, started_ts, last_notified_ts, "
                        "notify_count, message) VALUES (?, ?, ?, 0, 0, ?)",
                        (kind, int(critical), int(now), msg))
                    inc = self.db.query_one("SELECT * FROM incidents WHERE id = ?", (cur.lastrowid,))
                    self._notify(inc, msg, s, now)
                    if not critical:
                        self._last_warning[kind] = now
                else:
                    self.db.execute("UPDATE incidents SET message = ? WHERE id = ?", (msg, inc["id"]))
                    if (inc["critical"] and inc["acked_ts"] is None
                            and now - inc["last_notified_ts"] >= s.repeat_min * 60):
                        self._notify(inc, msg, s, now)

            for kind, inc in open_.items():
                if kind in conds or (stale and kind != "OFFLINE"):
                    continue
                self.db.execute("UPDATE incidents SET resolved_ts = ? WHERE id = ?",
                                (int(now), inc["id"]))
                if inc["critical"]:
                    mins = (now - inc["started_ts"]) / 60
                    self._event(now, f"{kind}_OK", f"Resuelto tras {mins:.0f} min")
                    results = self.notifier.send(
                        "✅ Cámara de curado: alarma resuelta",
                        f"Resuelto: {inc['message']} (duró {mins:.0f} min)")
                    log.info("Resuelto %s: %s", kind, results)

            unacked_critical = any(
                i["critical"] and i["acked_ts"] is None for i in self.open_incidents())
            if settings.HEARTBEAT_URL and not stale and not unacked_critical:
                self._heartbeat()

    def _notify(self, inc: dict, msg: str, s: AlarmSettings, now: float) -> None:
        critical = bool(inc["critical"])
        n = inc["notify_count"] + 1
        if critical:
            subject = "🚨 ALARMA cámara de curado" + (f" (aviso {n})" if n > 1 else "")
            body = (f"{msg}\n\nConfirma desde el panel (botón Reconocer) o pulsando 1 en la "
                    f"llamada para dejar de recibir avisos. Se repite cada {s.repeat_min:.0f} min.")
        else:
            subject, body = "⚠️ Aviso cámara de curado", msg
        results = self.notifier.send(subject, body, critical=critical,
                                     call=critical and s.call_on_critical)
        summary = ", ".join(f"{k}: {v}" for k, v in results.items()) or "sin canales configurados"
        self._event(now, inc["kind"], f"{msg} [{summary}]")
        self.db.execute(
            "UPDATE incidents SET last_notified_ts = ?, notify_count = ? WHERE id = ?",
            (int(now), n, inc["id"]))

    def _event(self, now: float, kind: str, message: str) -> None:
        log.warning("%s: %s", kind, message)
        self.db.execute("INSERT INTO events (ts, kind, message) VALUES (?, ?, ?)",
                        (int(now), kind, message))

    def _heartbeat(self) -> None:
        try:
            urllib.request.urlopen(settings.HEARTBEAT_URL, timeout=10).close()
        except OSError as e:
            log.error("Heartbeat falló: %s", e)

    def ack(self, by: str, now: float | None = None) -> int:
        """Acknowledges every open critical incident: repeats and calls stop."""
        now = time.time() if now is None else now
        with self._lock:
            pending = [i for i in self.open_incidents() if i["acked_ts"] is None]
            if not pending:
                return 0
            self.db.execute(
                "UPDATE incidents SET acked_ts = ?, acked_by = ? "
                "WHERE resolved_ts IS NULL AND acked_ts IS NULL", (int(now), by))
            kinds = ", ".join(i["kind"] for i in pending)
            self._event(now, "ACK", f"Alarma confirmada desde {by}: {kinds}")
            self.notifier.send("Cámara de curado: alarma confirmada",
                               f"Alguien ha confirmado la alarma desde {by} ({kinds}). "
                               "No se repetirán los avisos hasta que se resuelva o vuelva a ocurrir.")
            return len(pending)

    def test(self, call: bool) -> dict[str, str]:
        results = self.notifier.send(
            "Prueba de avisos de la cámara de curado",
            "Esto es una prueba del sistema de alarmas de la cámara de curado. "
            "Si recibes este mensaje, este canal funciona.",
            critical=False, call=call)
        self._event(time.time(), "TEST", ", ".join(f"{k}: {v}" for k, v in results.items())
                    or "sin canales configurados")
        return results

    # --- background loop ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(settings.ALERT_CHECK_S):
            try:
                self.check()
            except Exception:  # keep the checker alive whatever happens
                log.exception("Fallo comprobando alarmas")

    def start(self) -> None:
        if settings.ALERT_CHECK_S <= 0:
            return
        threading.Thread(target=self._run, name="alerts", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
