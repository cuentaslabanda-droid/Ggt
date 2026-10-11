#!/usr/bin/env python3
"""Fake ESP32 for trying the server without hardware.

    python tools/simulate_esp32.py --url http://localhost:8000 --token cambia-este-token

Stop with Ctrl+C.
"""

import argparse
import json
import math
import random
import time
import urllib.request


def post(url, token, body, content_type="application/json"):
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": content_type, "X-Device-Token": token}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--token", default="cambia-este-token")
    ap.add_argument("--period", type=float, default=5, help="seconds between reports")
    ap.add_argument("--photo", help="JPEG to upload as timelapse frame on every report")
    args = ap.parse_args()

    t, rh = 6.0, 75.0
    cfg = {"temp_c": 4, "rh": 80, "fan_duty": 30}
    version = 0
    comp = hum = False
    step = 0
    while True:
        # Crude physics: the fridge cools and dries, the humidifier wets,
        # the room warms it up slowly.
        comp = t > cfg["temp_c"] + 0.5 or (comp and t > cfg["temp_c"] - 0.5)
        hum = rh < cfg["rh"] - 3 or (hum and rh < cfg["rh"])
        t += (-0.15 if comp else 0.05) + random.gauss(0, 0.02)
        rh += (-0.4 if comp else 0.1) + (1.2 if hum else 0) + random.gauss(0, 0.2)
        rh = max(30, min(99, rh))
        wave = math.sin(step / 10)
        sensors = [
            {"name": "arriba", "ok": True, "t": round(t + 0.6 + 0.1 * wave, 2), "rh": round(rh - 2, 1)},
            {"name": "medio", "ok": True, "t": round(t, 2), "rh": round(rh, 1)},
            {"name": "abajo", "ok": True, "t": round(t - 0.5, 2), "rh": round(rh + 2, 1)},
        ]
        body = {
            "device": "simulador",
            "uptime": step * int(args.period),
            "rssi": -55,
            "cfg_version": version,
            "camera": True,
            "t": round(t, 2),
            "rh": round(rh, 1),
            "sensors": sensors,
            "outputs": {
                "compressor": comp,
                "humidifier": hum,
                "fan": 70 if comp or hum else cfg.get("fan_duty", 30),
                "cooling": comp,
                "dehumidifying": False,
            },
            "alarms": [],
        }
        resp = post(f"{args.url}/api/telemetry", args.token, json.dumps(body).encode())
        cfg, version = resp["config"], resp["version"]
        if args.photo:
            with open(args.photo, "rb") as f:
                post(f"{args.url}/api/photo", args.token, f.read(), "image/jpeg")
        print(f"T={t:.2f} HR={rh:.1f} comp={comp} hum={hum} -> consigna {cfg['temp_c']} C / {cfg['rh']} %")
        step += 1
        time.sleep(args.period)


if __name__ == "__main__":
    main()
