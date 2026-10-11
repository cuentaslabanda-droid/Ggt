"""Background checks: ESP32 offline and alarms reported by the controller."""

import logging
import threading
import time
import urllib.request

from . import settings
from .db import Database

log = logging.getLogger("curado.alerts")

ALARM_TEXT = {
    "SENSOR_FAIL": "Ningún sensor responde: nevera y humidificador apagados",
    "SENSOR_PARTIAL": "Algún sensor no responde",
    "TEMP_HIGH": "Temperatura demasiado alta",
    "TEMP_LOW": "Temperatura demasiado baja",
    "RH_HIGH": "Humedad demasiado alta",
    "RH_LOW": "Humedad demasiado baja",
}


class Alerter:
    def __init__(self, db: Database):
        self.db = db
        self._last_sent: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def notify(self, kind: str, message: str, now: float | None = None) -> bool:
        """Records an event and pushes it, unless the same kind was sent recently."""
        now = time.time() if now is None else now
        last = self._last_sent.get(kind)
        if last is not None and now - last < settings.ALERT_REPEAT_MIN * 60:
            return False
        self._last_sent[kind] = now
        self.db.execute(
            "INSERT INTO events (ts, kind, message) VALUES (?, ?, ?)", (int(now), kind, message)
        )
        log.warning("%s: %s", kind, message)
        if settings.ALERT_URL:
            try:
                req = urllib.request.Request(
                    settings.ALERT_URL,
                    data=message.encode(),
                    headers={"Title": "Cámara de curado", "Tags": "warning"},
                )
                urllib.request.urlopen(req, timeout=10).close()
            except OSError as e:
                log.error("No se pudo enviar la alerta: %s", e)
        return True

    def check(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        last = self.db.query_one("SELECT ts, alarms FROM readings ORDER BY ts DESC LIMIT 1")
        if last is None:
            return
        age_min = (now - last["ts"]) / 60
        if age_min > settings.OFFLINE_ALERT_MIN:
            self.notify("OFFLINE", f"La ESP32 no reporta desde hace {age_min:.0f} min", now)
            return
        for alarm in filter(None, (last["alarms"] or "").split(",")):
            self.notify(alarm, ALARM_TEXT.get(alarm, alarm), now)

    def _run(self) -> None:
        while not self._stop.wait(settings.ALERT_CHECK_S):
            try:
                self.check()
            except Exception:  # keep the checker alive whatever happens
                log.exception("Fallo comprobando alertas")

    def start(self) -> None:
        if settings.ALERT_CHECK_S <= 0:
            return
        self._thread = threading.Thread(target=self._run, name="alerts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
