"""
Arm metrics for Phoenix6 / hoot log data.
Analyzes arm TalonFX motor(s) and optional CANcoder.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import ARM_MOTORS, ARM_CANCODER, talon_key, cancoder_key

pd.options.mode.chained_assignment = None

LEADER_ID = ARM_MOTORS["leader"]


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Arm Max Current": ProcessMaxCurrent,
        "Arm Avg Current": ProcessAvgCurrent,
        "Arm Max Velocity": ProcessMaxVelocity,
        "Arm Min Supply Voltage": ProcessMinSupplyVoltage,
        "Arm CANcoder Position Range": ProcessPositionRange,
    }


def ProcessMaxCurrent(df: pd.DataFrame) -> Tuple[int, str]:
    key = talon_key(LEADER_ID, "StatorCurrent")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(50, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    max_val = float(smoothed.max())
    stoplight = 2 if max_val > 45 else (1 if max_val > 35 else 0)
    return stoplight, f"{max_val:.1f} A"


def ProcessAvgCurrent(df: pd.DataFrame) -> Tuple[int, str]:
    curr_key = talon_key(LEADER_ID, "StatorCurrent")
    volt_key = talon_key(LEADER_ID, "MotorVoltage")
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
    stoplight = 2 if avg_val > 10 else (1 if avg_val > 5 else 0)
    return stoplight, f"{avg_val:.1f} A"


def ProcessMaxVelocity(df: pd.DataFrame) -> Tuple[int, str]:
    key = talon_key(LEADER_ID, "Velocity")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    max_val = float(data.abs().max())
    return 0, f"{max_val:.1f} rot/s"


def ProcessMinSupplyVoltage(df: pd.DataFrame) -> Tuple[int, str]:
    key = talon_key(LEADER_ID, "SupplyVoltage")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(10, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    min_val = float(smoothed.min())
    stoplight = 2 if min_val < 7.0 else (1 if min_val < 8.5 else 0)
    return stoplight, f"{min_val:.2f} V"


def ProcessPositionRange(df: pd.DataFrame) -> Tuple[int, str]:
    """Report the range of motion seen by the arm CANcoder."""
    key = cancoder_key(ARM_CANCODER, "Position")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    pos_range = float(data.max() - data.min())
    return 0, f"{pos_range:.3f} rot ({pos_range * 360:.1f} deg)"
