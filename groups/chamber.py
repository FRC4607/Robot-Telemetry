"""
Chamber metrics for FRC 4607, 2026 (second robot).
Left Chamber: TalonFX ID 6, Right Chamber: TalonFX ID 17.
VelocityTorqueCurrentFOC, kMaxAmperage=80.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import (
    LEFT_CHAMBER_MOTOR, LEFT_CHAMBER_MAX_AMPERAGE,
    RIGHT_CHAMBER_MOTOR, RIGHT_CHAMBER_MAX_AMPERAGE,
    talon_key,
)
from config.metric_thresholds import get_threshold, high_is_bad
from metric_cache import get_numeric_cached

pd.options.mode.chained_assignment = None

CHAMBERS = {
    "Left": (LEFT_CHAMBER_MOTOR, LEFT_CHAMBER_MAX_AMPERAGE),
    "Right": (RIGHT_CHAMBER_MOTOR, RIGHT_CHAMBER_MAX_AMPERAGE),
}


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    return get_numeric_cached(df, key)


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    metrics = {}
    for side, (motor_id, max_amp) in CHAMBERS.items():
        metrics[f"{side} Chamber Max Current"] = (
            lambda df, d=motor_id, m=max_amp: _max_current(df, d, m)
        )
        metrics[f"{side} Chamber Avg Current"] = (
            lambda df, d=motor_id: _avg_current(df, d)
        )
        metrics[f"{side} Chamber Max Velocity"] = (
            lambda df, d=motor_id: _max_velocity(df, d)
        )
        metrics[f"{side} Chamber Velocity Error"] = (
            lambda df, d=motor_id: _velocity_error(df, d)
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
    warn_ratio = float(get_threshold("shared.max_current_warning_ratio", 0.75))
    stoplight = high_is_bad(max_val, max_amperage * warn_ratio, max_amperage)
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
        min_active_voltage = float(get_threshold("shared.motor_active_voltage_abs_min", 0.5))
        combined = combined[combined["volt"].abs() > min_active_voltage]
        if combined.empty:
            return 0, "0.0 A (motor inactive)"
        avg_val = float(combined["curr"].mean())
    else:
        avg_val = float(currents.mean())
    warning = float(get_threshold("chamber.avg_current.warning", 50.0))
    critical = float(get_threshold("chamber.avg_current.critical", 65.0))
    stoplight = high_is_bad(avg_val, warning, critical)
    return stoplight, f"{avg_val:.1f} A"


def _max_velocity(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    key = talon_key(device_id, "Velocity")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    max_val = float(data.abs().max())
    return 0, f"{max_val:.1f} rot/s"


def _velocity_error(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    """Mean |ClosedLoopError| when the chamber is actively spinning."""
    err = _get_numeric(df, talon_key(device_id, "ClosedLoopError"))
    ref = _get_numeric(df, talon_key(device_id, "ClosedLoopReference"))
    if err.empty or ref.empty:
        return -1, "no data (needs licensed owlet)"
    combined = pd.DataFrame({"err": err, "ref": ref}).interpolate(limit_direction="both").dropna()
    active = combined[combined["ref"].abs() > 1.0]
    if len(active) < 10:
        return 0, "no active spinning detected"
    mean_err = float(active["err"].abs().mean())
    peak_err = float(active["err"].abs().max())
    warning = float(get_threshold("chamber.velocity_error.warning", 2.0))
    critical = float(get_threshold("chamber.velocity_error.critical", 5.0))
    stoplight = high_is_bad(mean_err, warning, critical)
    return stoplight, f"avg {mean_err:.2f} rot/s, peak {peak_err:.1f} rot/s"
