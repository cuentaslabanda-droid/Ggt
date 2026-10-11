"""Builds timelapse videos from the stored photos with ffmpeg."""

import logging
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .db import Database

log = logging.getLogger("curado.timelapse")


class TimelapseBuilder:
    def __init__(self, db: Database, photos_dir: Path, videos_dir: Path):
        self.db = db
        self.photos_dir = photos_dir
        self.videos_dir = videos_dir
        self._lock = threading.Lock()
        self.status: dict = {"running": False, "last": None, "error": None}

    def start(self, start_ts: int, end_ts: int, fps: int, width: int) -> bool:
        """Starts a build in the background. False if one is already running."""
        with self._lock:
            if self.status["running"]:
                return False
            self.status = {"running": True, "last": None, "error": None}
        threading.Thread(
            target=self._build, args=(start_ts, end_ts, fps, width), daemon=True
        ).start()
        return True

    def _build(self, start_ts: int, end_ts: int, fps: int, width: int) -> None:
        try:
            output = self.build(start_ts, end_ts, fps, width)
            self.status = {"running": False, "last": output.name, "error": None}
        except Exception as e:
            log.exception("Timelapse falló")
            self.status = {"running": False, "last": None, "error": str(e)}

    def build(self, start_ts: int, end_ts: int, fps: int, width: int) -> Path:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg no está instalado (sudo apt install ffmpeg)")
        photos = self.db.query(
            "SELECT path FROM photos WHERE ts BETWEEN ? AND ? ORDER BY ts", (start_ts, end_ts)
        )
        if len(photos) < 2:
            raise RuntimeError("No hay suficientes fotos en ese rango")

        self.videos_dir.mkdir(parents=True, exist_ok=True)
        output = self.videos_dir / time.strftime("timelapse_%Y%m%d_%H%M%S.mp4")
        with tempfile.TemporaryDirectory() as tmp:
            # ffmpeg wants a numbered sequence: symlink the photos in order.
            n = 0
            for row in photos:
                src = self.photos_dir / row["path"]
                if src.exists():
                    n += 1
                    (Path(tmp) / f"{n:06d}.jpg").symlink_to(src.resolve())
            if n < 2:
                raise RuntimeError("Faltan los archivos de las fotos")
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-framerate", str(fps), "-i", f"{tmp}/%06d.jpg",
                "-vf", f"scale={width}:-2,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-movflags", "+faststart", str(output),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg: {result.stderr.strip()[-500:]}")
        log.info("Timelapse %s (%d fotos)", output.name, n)
        return output
