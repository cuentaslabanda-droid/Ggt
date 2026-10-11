"""Runtime configuration, read from environment variables."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Shared secret the ESP32 sends in the X-Device-Token header.
DEVICE_TOKEN = os.environ.get("CURADO_TOKEN", "cambia-este-token")

DATA_DIR = Path(os.environ.get("CURADO_DATA", BASE_DIR / "data"))
PROGRAMS_DIR = Path(os.environ.get("CURADO_PROGRAMS", BASE_DIR / "programs"))

# Notification channels (email, Telegram, WhatsApp, calls...) are read from the
# environment by notify.build_channels(); see deploy/curado.env.example.

# Dead man's switch: pinged every minute while everything is fine. A service
# like healthchecks.io then warns you if the Pi itself dies or loses power.
HEARTBEAT_URL = os.environ.get("CURADO_HEARTBEAT_URL", "")

# Raise an alert when the ESP32 has not reported for this long.
OFFLINE_ALERT_MIN = float(os.environ.get("CURADO_OFFLINE_MIN", "10"))

# Seconds between alert checks; 0 disables the background checker.
ALERT_CHECK_S = float(os.environ.get("CURADO_ALERT_CHECK_S", "60"))

# A warning (not critical) is not repeated more often than this.
ALERT_REPEAT_MIN = float(os.environ.get("CURADO_ALERT_REPEAT_MIN", "60"))

# Keep this many days of telemetry (0 = forever).
RETENTION_DAYS = int(os.environ.get("CURADO_RETENTION_DAYS", "0"))
