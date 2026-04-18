"""
Intake Wheels metrics for FRC 4607, 2026 (second robot).
TalonFX ID 14, VelocityTorqueCurrentFOC, kMaxAmperage=80.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import INTAKE_WHEELS_MOTOR, INTAKE_WHEELS_MAX_AMPERAGE, talon_key

pd.options.mode.chained_assignment = None

MOTOR_ID = INTAKE_WHEELS_MOTOR


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Intake Wheels Max Current": ProcessMaxCurrent,
        "Intake Wheels Avg Current": ProcessAvgCurrent,
        "Intake Wheels Max Velocity": ProcessMaxVelocity,
        "Intake Wheels Velocity Error": ProcessVelocityError,
    }


def ProcessMaxCurrent(df: pd.DataFrame) -> Tuple[int, str]:
    key = talon_key(MOTOR_ID, "StatorCurrent")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(50, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    max_val = float(smoothed.max())
    stoplight = 2 if max_val > INTAKE_WHEELS_MAX_AMPERAGE else (1 if max_val > INTAKE_WHEELS_MAX_AMPERAGE * 0.75 else 0)
    return stoplight, f"{max_val:.1f} A"


def ProcessAvgCurrent(df: pd.DataFrame) -> Tuple[int, str]:
    curr_key = talon_key(MOTOR_ID, "StatorCurrent")
    volt_key = talon_key(MOTOR_ID, "MotorVoltage")
    currents = _get_numeric(df, curr_key)
    voltages = _get_numeric(df, volt_key)
    if currents.empty:
        return -1, "metric_not_implemented"
    if not voltages.empty:
        combined = pd.DataFrame({"curr": currents, "volt": voltages}).interpolate(
            limit_direction="both"
        )
        combined = combined[combined["volt"].abs() > 0.5]
        if combined.empty:
            return 0, "0.0 A (motor inactive)"
        avg_val = float(combined["curr"].mean())
    else:
        avg_val = float(currents.mean())
    stoplight = 2 if avg_val > 20 else (1 if avg_val > 10 else 0)
    return stoplight, f"{avg_val:.1f} A"


def ProcessMaxVelocity(df: pd.DataFrame) -> Tuple[int, str]:
    key = talon_key(MOTOR_ID, "Velocity")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    max_val = float(data.abs().max())
    return 0, f"{max_val:.1f} rot/s"


def ProcessVelocityError(df: pd.DataFrame) -> Tuple[int, str]:
    """Mean |ClosedLoopError| when the intake wheels are actively spinning."""
    err = _get_numeric(df, talon_key(MOTOR_ID, "ClosedLoopError"))
    ref = _get_numeric(df, talon_key(MOTOR_ID, "ClosedLoopReference"))
    if err.empty or ref.empty:
        return -1, "no data (needs licensed owlet)"
    combined = pd.DataFrame({"err": err, "ref": ref}).interpolate(limit_direction="both").dropna()
    active = combined[combined["ref"].abs() > 1.0]
    if len(active) < 10:
        return 0, "no active spinning detected"
    mean_err = float(active["err"].abs().mean())
    peak_err = float(active["err"].abs().max())
    stoplight = 2 if mean_err > 5.0 else (1 if mean_err > 2.0 else 0)
    return stoplight, f"avg {mean_err:.2f} rot/s, peak {peak_err:.1f} rot/s"
