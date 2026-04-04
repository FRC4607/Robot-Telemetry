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


def _try_key(df, motor_id, signal):
    """Try TalonFX key first, then TalonFXS."""
    for prefix in ["Phoenix6/TalonFX", "Phoenix6/TalonFXS"]:
        key = f"{prefix}-{motor_id}/{signal}"
        data = _get_numeric(df, key)
        if not data.empty:
            return data
    return pd.Series(dtype=float)


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    metrics = {}
    for side, (motor_id, max_amp) in HOODS.items():
        metrics[f"{side} Hood Max Current"] = (
            lambda df, d=motor_id, m=max_amp: _max_current(df, d, m)
        )
        metrics[f"{side} Hood Position Range"] = (
            lambda df, d=motor_id: _position_range(df, d)
        )
    return metrics


def _max_current(df: pd.DataFrame, device_id: int, max_amperage: float) -> Tuple[int, str]:
    data = _try_key(df, device_id, "StatorCurrent")
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(50, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    max_val = float(smoothed.max())
    stoplight = 2 if max_val > max_amperage else (1 if max_val > max_amperage * 0.75 else 0)
    return stoplight, f"{max_val:.1f} A"


def _position_range(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    data = _try_key(df, device_id, "Position")
    if data.empty:
        return -1, "metric_not_implemented"
    min_pos = float(data.min())
    max_pos = float(data.max())
    return 0, f"{min_pos:.2f} to {max_pos:.2f} rot"
