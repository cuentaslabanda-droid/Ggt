"""Runtime configuration, read from environment variables."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Shared secret the ESP32 sends in the X-Device-Token header.
DEVICE_TOKEN = os.environ.get("CURADO_TOKEN", "cambia-este-token")

DATA_DIR = Path(os.environ.get("CURADO_DATA", BASE_DIR / "data"))
PROGRAMS_DIR = Path(os.environ.get("CURADO_PROGRAMS", BASE_DIR / "programs"))

# Optional push notifications. Any URL that accepts a plain-text POST works;
# ntfy.sh is the easiest: https://ntfy.sh/<tu-tema-secreto>
ALERT_URL = os.environ.get("CURADO_ALERT_URL", "")

# Raise an alert when the ESP32 has not reported for this long.
OFFLINE_ALERT_MIN = float(os.environ.get("CURADO_OFFLINE_MIN", "10"))

# Seconds between alert checks; 0 disables the background checker.
ALERT_CHECK_S = float(os.environ.get("CURADO_ALERT_CHECK_S", "60"))

# Same alert is not repeated more often than this.
ALERT_REPEAT_MIN = float(os.environ.get("CURADO_ALERT_REPEAT_MIN", "60"))

# Keep this many days of telemetry (0 = forever).
RETENTION_DAYS = int(os.environ.get("CURADO_RETENTION_DAYS", "0"))
