"""
Hood metrics for Slap Shot (FRC 4607, 2026).
TalonFXS ID 52 (Minion motor), MotionMagicVoltage, kMaxAmperage=80.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import HOOD_MOTOR, HOOD_MAX_AMPERAGE, talon_key

pd.options.mode.chained_assignment = None

MOTOR_ID = HOOD_MOTOR


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Hood Max Current": ProcessMaxCurrent,
        "Hood Position Range": ProcessPositionRange,
    }


def ProcessMaxCurrent(df: pd.DataFrame) -> Tuple[int, str]:
    # Try TalonFX key first, then TalonFXS
    for prefix in ["Phoenix6/TalonFX", "Phoenix6/TalonFXS"]:
        key = f"{prefix}-{MOTOR_ID}/StatorCurrent"
        data = _get_numeric(df, key)
        if not data.empty:
            break
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(50, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    max_val = float(smoothed.max())
    stoplight = 2 if max_val > HOOD_MAX_AMPERAGE else (1 if max_val > HOOD_MAX_AMPERAGE * 0.75 else 0)
    return stoplight, f"{max_val:.1f} A"


def ProcessPositionRange(df: pd.DataFrame) -> Tuple[int, str]:
    """Report hood position range (motor rotations)."""
    for prefix in ["Phoenix6/TalonFX", "Phoenix6/TalonFXS"]:
        key = f"{prefix}-{MOTOR_ID}/Position"
        data = _get_numeric(df, key)
        if not data.empty:
            break
    if data.empty:
        return -1, "metric_not_implemented"
    min_pos = float(data.min())
    max_pos = float(data.max())
    return 0, f"{min_pos:.2f} to {max_pos:.2f} rot"
