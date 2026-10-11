"""Curing programs: a sequence of phases, each with its own climate.

A program is a JSON file in server/programs/:

    {
      "name": "Jamón serrano",
      "description": "...",
      "phases": [
        {"name": "Salado", "days": 10, "temp_c": 3, "rh": 88, "fan_duty": 20},
        {"name": "Secado", "days": 60, "temp_c": 12, "rh": 76, "fan_duty": 35,
         "ramp_days": 14},
        ...
      ]
    }

ramp_days makes the change from the previous phase gradual: temperature,
humidity and fan move linearly over the first ramp_days of the phase, which
avoids case hardening ("costra") from sudden drops in humidity.
"""

import json
from dataclasses import dataclass
from pathlib import Path

RAMPED_KEYS = ("temp_c", "rh", "fan_duty")


@dataclass
class ProgramState:
    phase_index: int
    phase_name: str
    day: float            # days since the program started
    phase_day: float      # days since the current phase started
    phase_days: float     # length of the current phase
    finished: bool        # past the last phase (its climate is held)
    setpoints: dict

    def as_dict(self) -> dict:
        return {
            "phase_index": self.phase_index,
            "phase_name": self.phase_name,
            "day": round(self.day, 2),
            "phase_day": round(self.phase_day, 2),
            "phase_days": self.phase_days,
            "finished": self.finished,
            "setpoints": self.setpoints,
        }


def load_programs(directory: Path) -> dict[str, dict]:
    programs = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        validate(data, path.name)
        programs[path.stem] = data
    return programs


def validate(program: dict, label: str = "program") -> None:
    phases = program.get("phases")
    if not phases:
        raise ValueError(f"{label}: no phases")
    for i, phase in enumerate(phases):
        for key in ("name", "days", *RAMPED_KEYS):
            if key not in phase:
                raise ValueError(f"{label}: phase {i} lacks '{key}'")
        if phase["days"] <= 0:
            raise ValueError(f"{label}: phase {i} must last at least one day")
        if phase.get("ramp_days", 0) > phase["days"]:
            raise ValueError(f"{label}: phase {i} ramp longer than the phase")


def evaluate(program: dict, elapsed_days: float) -> ProgramState:
    """Setpoints of a program after elapsed_days."""
    phases = program["phases"]
    elapsed_days = max(0.0, elapsed_days)
    start = 0.0
    for i, phase in enumerate(phases):
        end = start + phase["days"]
        last = i == len(phases) - 1
        if elapsed_days < end or last:
            phase_day = elapsed_days - start
            setpoints = {k: phase[k] for k in RAMPED_KEYS}
            ramp = phase.get("ramp_days", 0)
            if i > 0 and ramp > 0 and phase_day < ramp:
                prev = phases[i - 1]
                f = phase_day / ramp
                setpoints = {k: prev[k] + (phase[k] - prev[k]) * f for k in RAMPED_KEYS}
            setpoints = {
                "temp_c": round(setpoints["temp_c"], 2),
                "rh": round(setpoints["rh"], 1),
                "fan_duty": int(round(setpoints["fan_duty"])),
            }
            return ProgramState(
                phase_index=i,
                phase_name=phase["name"],
                day=elapsed_days,
                phase_day=phase_day,
                phase_days=phase["days"],
                finished=elapsed_days >= end,
                setpoints=setpoints,
            )
        start = end
    raise AssertionError("unreachable")


def total_days(program: dict) -> float:
    return sum(p["days"] for p in program["phases"])
