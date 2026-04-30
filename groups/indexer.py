"""
Indexer metrics for FRC 4607, 2026 (second robot).
TalonFX ID 13, VelocityTorqueCurrentFOC, kMaxAmperage=80.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import INDEXER_MOTOR, INDEXER_MAX_AMPERAGE, talon_key
from config.metric_thresholds import get_threshold, high_is_bad
from metric_cache import get_numeric_cached

pd.options.mode.chained_assignment = None

MOTOR_ID = INDEXER_MOTOR


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    return get_numeric_cached(df, key)


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Indexer Max Current": ProcessMaxCurrent,
        "Indexer Avg Current": ProcessAvgCurrent,
        "Indexer Max Velocity": ProcessMaxVelocity,
        "Indexer Velocity Error": ProcessVelocityError,
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
    warn_ratio = float(get_threshold("shared.max_current_warning_ratio", 0.75))
    stoplight = high_is_bad(max_val, INDEXER_MAX_AMPERAGE * warn_ratio, INDEXER_MAX_AMPERAGE)
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
        min_active_voltage = float(get_threshold("shared.motor_active_voltage_abs_min", 0.5))
        combined = combined[combined["volt"].abs() > min_active_voltage]
        if combined.empty:
            return 0, "0.0 A (motor inactive)"
        avg_val = float(combined["curr"].mean())
    else:
        avg_val = float(currents.mean())
    warning = float(get_threshold("indexer.avg_current.warning", 30.0))
    critical = float(get_threshold("indexer.avg_current.critical", 40.0))
    stoplight = high_is_bad(avg_val, warning, critical)
    return stoplight, f"{avg_val:.1f} A"


def ProcessMaxVelocity(df: pd.DataFrame) -> Tuple[int, str]:
    key = talon_key(MOTOR_ID, "Velocity")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    max_val = float(data.abs().max())
    return 0, f"{max_val:.1f} rot/s"


def ProcessVelocityError(df: pd.DataFrame) -> Tuple[int, str]:
    """Mean |ClosedLoopError| when the indexer is actively spinning."""
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
    warning = float(get_threshold("indexer.velocity_error.warning", 2.0))
    critical = float(get_threshold("indexer.velocity_error.critical", 5.0))
    stoplight = high_is_bad(mean_err, warning, critical)
    return stoplight, f"avg {mean_err:.2f} rot/s, peak {peak_err:.1f} rot/s"
