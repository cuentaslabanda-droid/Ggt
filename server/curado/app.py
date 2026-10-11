"""Servidor de la cámara de curado (Raspberry Pi).

Run:  uvicorn curado.app:app --host 0.0.0.0 --port 8000
"""

import json
import logging
import time
import zlib
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import programs as programs_mod
from . import settings
from .alerts import Alerter
from .db import Database
from .timelapse import TimelapseBuilder

log = logging.getLogger("curado")
STATIC_DIR = Path(__file__).parent / "static"
MAX_PHOTO_BYTES = 5 * 1024 * 1024

Mode = Literal["auto", "on", "off"]


class Offset(BaseModel):
    t: float = Field(0, ge=-5, le=5)
    rh: float = Field(0, ge=-15, le=15)


class Setpoints(BaseModel):
    """Everything the ESP32 receives. Field names match the firmware."""

    temp_c: float = Field(4, ge=0, le=30)
    temp_hyst: float = Field(0.5, ge=0.1, le=5)
    rh: float = Field(75, ge=40, le=95)
    rh_hyst: float = Field(3, ge=0.5, le=15)
    dehum_margin: float = Field(1.5, ge=0, le=6)
    min_temp_c: float = Field(1, ge=-2, le=15)
    fan_duty: int = Field(30, ge=0, le=100)
    fan_boost_duty: int = Field(70, ge=0, le=100)
    spread_t: float = Field(1.5, ge=0.2, le=10)
    spread_rh: float = Field(6, ge=1, le=30)
    comp_min_on: int = Field(120, ge=30, le=1800)
    comp_min_off: int = Field(300, ge=60, le=3600)
    hum_min_on: int = Field(10, ge=1, le=600)
    hum_min_off: int = Field(30, ge=5, le=3600)
    hum_max_on: int = Field(120, ge=5, le=3600)
    comp_mode: Mode = "auto"
    hum_mode: Mode = "auto"
    fan_mode: Mode = "auto"
    fan_manual: int = Field(50, ge=0, le=100)
    photo_interval: int = Field(900, ge=0, le=86400)
    flash: bool = True
    offsets: list[Offset] = Field(default_factory=lambda: [Offset() for _ in range(3)])

    @model_validator(mode="after")
    def check(self):
        if 0 < self.photo_interval < 60:
            raise ValueError("photo_interval: 0 (desactivado) o al menos 60 s")
        if len(self.offsets) != 3:
            raise ValueError("offsets: hacen falta 3 (uno por sensor)")
        if self.hum_max_on < self.hum_min_on:
            raise ValueError("hum_max_on debe ser >= hum_min_on")
        return self


class Settings(BaseModel):
    # manual: setpoints as written. program: temp_c, rh and fan_duty follow the
    # curing program started on program_start.
    mode: Literal["manual", "program"] = "manual"
    program: str | None = None
    program_start: date | None = None
    setpoints: Setpoints = Field(default_factory=Setpoints)


class HamIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    initial_weight_g: float = Field(gt=0)
    target_loss_pct: float = Field(33, gt=0, lt=80)
    notes: str = ""


class WeightIn(BaseModel):
    weight_g: float = Field(gt=0)
    ts: int | None = None


class TimelapseIn(BaseModel):
    days: float = Field(7, gt=0, le=3650)
    fps: int = Field(24, ge=1, le=60)
    width: int = Field(1280, ge=320, le=1920)


def create_app(data_dir: Path | None = None, programs_dir: Path | None = None) -> FastAPI:
    data_dir = Path(data_dir or settings.DATA_DIR)
    photos_dir = data_dir / "photos"
    videos_dir = data_dir / "timelapse"
    photos_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)

    db = Database(data_dir / "curado.db")
    programs = programs_mod.load_programs(Path(programs_dir or settings.PROGRAMS_DIR))
    alerter = Alerter(db)
    timelapse = TimelapseBuilder(db, photos_dir, videos_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        alerter.start()
        yield
        alerter.stop()

    app = FastAPI(title="Cámara de curado", lifespan=lifespan)
    app.state.db = db
    app.state.alerter = alerter

    # --- helpers -----------------------------------------------------------

    def load_settings() -> Settings:
        return Settings.model_validate(db.get_json("settings", {}))

    def program_state(s: Settings, now: float) -> dict | None:
        if s.mode != "program" or not s.program or not s.program_start:
            return None
        program = programs.get(s.program)
        if program is None:
            return None
        start = datetime.combine(s.program_start, datetime.min.time()).timestamp()
        state = programs_mod.evaluate(program, (now - start) / 86400).as_dict()
        state["program"] = s.program
        state["program_name"] = program["name"]
        state["total_days"] = programs_mod.total_days(program)
        return state

    def effective_config(now: float) -> tuple[dict, int, dict | None]:
        """Config sent to the ESP32 and its version (a hash of the content)."""
        s = load_settings()
        cfg = s.setpoints.model_dump()
        state = program_state(s, now)
        if state:
            cfg.update(state["setpoints"])
        version = zlib.crc32(json.dumps(cfg, sort_keys=True).encode()) or 1
        return cfg, version, state

    def check_token(token: str | None) -> None:
        if token != settings.DEVICE_TOKEN:
            raise HTTPException(401, "token inválido")

    def ham_summary(ham: dict) -> dict:
        weights = db.query(
            "SELECT id, ts, weight_g FROM weights WHERE ham_id = ? ORDER BY ts", (ham["id"],)
        )
        current = weights[-1]["weight_g"] if weights else ham["initial_weight_g"]
        loss = 100 * (1 - current / ham["initial_weight_g"])
        return {
            **ham,
            "weights": weights,
            "current_weight_g": current,
            "loss_pct": round(loss, 1),
            "target_weight_g": round(ham["initial_weight_g"] * (1 - ham["target_loss_pct"] / 100)),
            "done": loss >= ham["target_loss_pct"],
        }

    # --- ESP32 endpoints ---------------------------------------------------

    @app.post("/api/telemetry")
    async def telemetry(request: Request, x_device_token: str | None = Header(None)):
        check_token(x_device_token)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "JSON inválido")
        now = time.time()
        sensors = (body.get("sensors") or []) + [{}] * 3
        outputs = body.get("outputs") or {}

        def sensor_val(i: int, key: str):
            s = sensors[i]
            return s.get(key) if s.get("ok") else None

        db.execute(
            "INSERT INTO readings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(now), body.get("t"), body.get("rh"),
                sensor_val(0, "t"), sensor_val(0, "rh"),
                sensor_val(1, "t"), sensor_val(1, "rh"),
                sensor_val(2, "t"), sensor_val(2, "rh"),
                int(bool(outputs.get("compressor"))), int(bool(outputs.get("humidifier"))),
                int(outputs.get("fan") or 0),
                int(bool(outputs.get("cooling"))), int(bool(outputs.get("dehumidifying"))),
                ",".join(body.get("alarms") or []), body.get("rssi"), body.get("uptime"),
            ),
        )
        db.set_json("device", {
            "ts": int(now),
            "device": body.get("device"),
            "uptime": body.get("uptime"),
            "rssi": body.get("rssi"),
            "camera": body.get("camera"),
            "cfg_version": body.get("cfg_version"),
            "sensor_names": [s.get("name") for s in sensors[:3]],
        })
        if settings.RETENTION_DAYS > 0 and int(now) % 3600 < 30:
            db.execute("DELETE FROM readings WHERE ts < ?", (now - settings.RETENTION_DAYS * 86400,))

        cfg, version, _ = effective_config(now)
        return {"version": version, "config": cfg}

    @app.post("/api/photo")
    async def photo(request: Request, x_device_token: str | None = Header(None)):
        check_token(x_device_token)
        data = await request.body()
        if not data.startswith(b"\xff\xd8") or len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(400, "se esperaba un JPEG")
        now = time.time()
        rel = Path(time.strftime("%Y-%m-%d", time.localtime(now))) / time.strftime(
            "%H%M%S.jpg", time.localtime(now)
        )
        (photos_dir / rel.parent).mkdir(parents=True, exist_ok=True)
        (photos_dir / rel).write_bytes(data)
        db.execute(
            "INSERT INTO photos (ts, path, size) VALUES (?, ?, ?)", (int(now), str(rel), len(data))
        )
        return {"ok": True, "path": str(rel)}

    # --- Dashboard API ---------------------------------------------------------

    @app.get("/api/status")
    def status():
        now = time.time()
        last = db.query_one("SELECT * FROM readings ORDER BY ts DESC LIMIT 1")
        cfg, version, state = effective_config(now)
        device = db.get_json("device")
        duty = db.query_one(
            "SELECT AVG(compressor) AS comp, AVG(humidifier) AS hum, COUNT(*) AS n "
            "FROM readings WHERE ts > ?",
            (now - 86400,),
        )
        photo_row = db.query_one("SELECT ts, path FROM photos ORDER BY ts DESC LIMIT 1")
        return {
            "now": int(now),
            "reading": last,
            "online": bool(last and now - last["ts"] < settings.OFFLINE_ALERT_MIN * 60),
            "device": device,
            "config": cfg,
            "config_version": version,
            "config_synced": bool(device and device.get("cfg_version") == version),
            "program": state,
            "duty_24h": {
                "compressor": round(100 * (duty["comp"] or 0), 1),
                "humidifier": round(100 * (duty["hum"] or 0), 1),
                "samples": duty["n"],
            },
            "latest_photo": photo_row,
            "events": db.query("SELECT ts, kind, message FROM events ORDER BY ts DESC LIMIT 10"),
        }

    @app.get("/api/history")
    def history(hours: float = 24, points: int = 600):
        hours = min(max(hours, 0.1), 24 * 400)
        points = min(max(points, 10), 5000)
        now = time.time()
        bucket = max(30, int(hours * 3600 / points))
        rows = db.query(
            f"""SELECT (ts / {bucket}) * {bucket} AS ts,
                   ROUND(AVG(t), 2) AS t, ROUND(AVG(rh), 1) AS rh,
                   ROUND(AVG(s0_t), 2) AS s0_t, ROUND(AVG(s0_rh), 1) AS s0_rh,
                   ROUND(AVG(s1_t), 2) AS s1_t, ROUND(AVG(s1_rh), 1) AS s1_rh,
                   ROUND(AVG(s2_t), 2) AS s2_t, ROUND(AVG(s2_rh), 1) AS s2_rh,
                   ROUND(100 * AVG(compressor)) AS compressor,
                   ROUND(100 * AVG(humidifier)) AS humidifier,
                   ROUND(AVG(fan)) AS fan
               FROM readings WHERE ts > ? GROUP BY ts / {bucket} ORDER BY ts""",
            (now - hours * 3600,),
        )
        return {"bucket_s": bucket, "rows": rows}

    @app.get("/api/settings")
    def get_settings():
        return load_settings()

    @app.put("/api/settings")
    def put_settings(new: Settings):
        if new.mode == "program":
            if new.program not in programs:
                raise HTTPException(400, f"programa desconocido: {new.program}")
            if new.program_start is None:
                new.program_start = date.today()
        db.set_json("settings", new.model_dump(mode="json"))
        cfg, version, state = effective_config(time.time())
        return {"settings": new, "config": cfg, "version": version, "program": state}

    @app.get("/api/programs")
    def list_programs():
        return {
            key: {**p, "total_days": programs_mod.total_days(p)} for key, p in programs.items()
        }

    # --- Hams and weight loss -------------------------------------------------

    @app.get("/api/hams")
    def list_hams(archived: bool = False):
        hams = db.query(
            "SELECT * FROM hams WHERE archived = ? ORDER BY created_ts", (int(archived),)
        )
        return [ham_summary(h) for h in hams]

    @app.post("/api/hams")
    def create_ham(ham: HamIn):
        cur = db.execute(
            "INSERT INTO hams (name, created_ts, initial_weight_g, target_loss_pct, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (ham.name, int(time.time()), ham.initial_weight_g, ham.target_loss_pct, ham.notes),
        )
        return ham_summary(db.query_one("SELECT * FROM hams WHERE id = ?", (cur.lastrowid,)))

    def get_ham(ham_id: int) -> dict:
        ham = db.query_one("SELECT * FROM hams WHERE id = ?", (ham_id,))
        if ham is None:
            raise HTTPException(404, "jamón no encontrado")
        return ham

    @app.post("/api/hams/{ham_id}/weights")
    def add_weight(ham_id: int, w: WeightIn):
        get_ham(ham_id)
        db.execute(
            "INSERT INTO weights (ham_id, ts, weight_g) VALUES (?, ?, ?)",
            (ham_id, w.ts or int(time.time()), w.weight_g),
        )
        return ham_summary(get_ham(ham_id))

    @app.delete("/api/hams/{ham_id}/weights/{weight_id}")
    def delete_weight(ham_id: int, weight_id: int):
        db.execute("DELETE FROM weights WHERE id = ? AND ham_id = ?", (weight_id, ham_id))
        return ham_summary(get_ham(ham_id))

    @app.post("/api/hams/{ham_id}/archive")
    def archive_ham(ham_id: int):
        get_ham(ham_id)
        db.execute("UPDATE hams SET archived = 1 WHERE id = ?", (ham_id,))
        return {"ok": True}

    # --- Photos and timelapse ---------------------------------------------------

    @app.get("/api/photos")
    def list_photos(limit: int = 48, before: int | None = None):
        limit = min(max(limit, 1), 500)
        return db.query(
            "SELECT ts, path, size FROM photos WHERE ts < ? ORDER BY ts DESC LIMIT ?",
            (before or 2**62, limit),
        )

    @app.get("/api/timelapse")
    def timelapse_status():
        videos = sorted(videos_dir.glob("*.mp4"), reverse=True)
        return {
            "status": timelapse.status,
            "videos": [{"name": v.name, "size": v.stat().st_size} for v in videos],
        }

    @app.post("/api/timelapse")
    def timelapse_build(req: TimelapseIn):
        end = int(time.time())
        if not timelapse.start(end - int(req.days * 86400), end, req.fps, req.width):
            raise HTTPException(409, "ya se está generando un timelapse")
        return {"ok": True}

    app.mount("/photos", StaticFiles(directory=photos_dir), name="photos")
    app.mount("/videos", StaticFiles(directory=videos_dir), name="videos")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
app = create_app()
