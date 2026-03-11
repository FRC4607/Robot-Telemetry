"""
Manipulator metrics for Phoenix6 / hoot log data.
Analyzes intake and scorer TalonFX motors.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import MANIPULATOR_MOTORS, talon_key

pd.options.mode.chained_assignment = None


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    metrics = {}
    for role, device_id in MANIPULATOR_MOTORS.items():
        name = role.capitalize()
        metrics[f"{name} Max Current"] = (
            lambda df, d=device_id: _max_current(df, d)
        )
        metrics[f"{name} Avg Current"] = (
            lambda df, d=device_id: _avg_current(df, d)
        )
    return metrics


def _max_current(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    key = talon_key(device_id, "StatorCurrent")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(50, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    max_val = float(smoothed.max())
    stoplight = 2 if max_val > 50 else (1 if max_val > 45 else 0)
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
    stoplight = 2 if avg_val > 15 else (1 if avg_val > 8 else 0)
    return stoplight, f"{avg_val:.1f} A"
