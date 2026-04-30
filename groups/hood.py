"""
Hood metrics for FRC 4607, 2026 (second robot).
Left Hood: TalonFX ID 8, Right Hood: TalonFX ID 9.
kMaxAmperage=80.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import (
    LEFT_HOOD_MOTOR, LEFT_HOOD_MAX_AMPERAGE,
    RIGHT_HOOD_MOTOR, RIGHT_HOOD_MAX_AMPERAGE,
    talon_key,
)

pd.options.mode.chained_assignment = None

HOODS = {
    "Left": (LEFT_HOOD_MOTOR, LEFT_HOOD_MAX_AMPERAGE),
    "Right": (RIGHT_HOOD_MOTOR, RIGHT_HOOD_MAX_AMPERAGE),
}


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    metrics = {}
    for side, (motor_id, max_amp) in HOODS.items():
        metrics[f"{side} Hood Max Current"] = (
            lambda df, d=motor_id, m=max_amp: _max_current(df, d, m)
        )
        metrics[f"{side} Hood Avg Current"] = (
            lambda df, d=motor_id: _avg_current(df, d)
        )
        metrics[f"{side} Hood Position Range"] = (
            lambda df, d=motor_id: _position_range(df, d)
        )
        metrics[f"{side} Hood Position Error"] = (
            lambda df, d=motor_id: _position_error(df, d)
        )
    return metrics


def _max_current(df: pd.DataFrame, device_id: int, max_amperage: float) -> Tuple[int, str]:
    key = talon_key(device_id, "StatorCurrent")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(50, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    max_val = float(smoothed.max())
    stoplight = 2 if max_val > max_amperage else (1 if max_val > max_amperage * 0.75 else 0)
    return stoplight, f"{max_val:.1f} A"


def _avg_current(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    curr_key = talon_key(device_id, "StatorCurrent")
    volt_key = talon_key(device_id, "MotorVoltage")
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
    stoplight = 2 if avg_val > 30 else (1 if avg_val > 18 else 0)
    return stoplight, f"{avg_val:.1f} A"


def _position_range(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    key = talon_key(device_id, "Position")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    min_pos = float(data.min())
    max_pos = float(data.max())
    return 0, f"{min_pos:.2f} to {max_pos:.2f} rot"


def _position_error(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    """Mean |ClosedLoopError| when the hood is actively positioning."""
    err = _get_numeric(df, talon_key(device_id, "ClosedLoopError"))
    ref = _get_numeric(df, talon_key(device_id, "ClosedLoopReference"))
    if err.empty or ref.empty:
        return -1, "no data (needs licensed owlet)"
    combined = pd.DataFrame({"err": err, "ref": ref}).interpolate(limit_direction="both").dropna()
    ref_diff = combined["ref"].diff().abs()
    active = combined[ref_diff > 0.0001]
    if len(active) < 10:
        active = combined[combined["err"].abs() > 0.0001]
    if len(active) < 10:
        return 0, "no active positioning detected"
    mean_err = float(active["err"].abs().mean())
    peak_err = float(active["err"].abs().max())
    mean_deg = mean_err * 360.0
    peak_deg = peak_err * 360.0
    stoplight = 2 if mean_deg > 5.0 else (1 if mean_deg > 2.0 else 0)
    return stoplight, f"avg {mean_deg:.2f} deg, peak {peak_deg:.1f} deg"
