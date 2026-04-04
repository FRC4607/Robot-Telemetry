"""
Intake Arm metrics for FRC 4607, 2026 (second robot).
TalonFX ID 15 + CANcoder ID 15, DynamicMotionMagicTorqueCurrentFOC.
SensorToMech=2, RotorToSensor=23, kMaxAmperage=80.
Soft limits: forward=0.19, reverse=0.02 (mechanism rotations).
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import (
    INTAKE_ARM_MOTOR, INTAKE_ARM_CANCODER, INTAKE_ARM_MAX_AMPERAGE,
    talon_key, cancoder_key,
)

pd.options.mode.chained_assignment = None

MOTOR_ID = INTAKE_ARM_MOTOR
CANCODER_ID = INTAKE_ARM_CANCODER


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Intake Arm Max Current": ProcessMaxCurrent,
        "Intake Arm Avg Current": ProcessAvgCurrent,
        "Intake Arm Max Velocity": ProcessMaxVelocity,
        "Intake Arm Position Range": ProcessPositionRange,
        "Intake Arm Encoder Alignment": ProcessEncoderAlignment,
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
    stoplight = 2 if max_val > INTAKE_ARM_MAX_AMPERAGE else (1 if max_val > INTAKE_ARM_MAX_AMPERAGE * 0.75 else 0)
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


def ProcessPositionRange(df: pd.DataFrame) -> Tuple[int, str]:
    """Report range of motion seen by the intake arm CANcoder."""
    key = cancoder_key(CANCODER_ID, "Position")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    min_pos = float(data.min())
    max_pos = float(data.max())
    return 0, f"{min_pos:.3f} to {max_pos:.3f} rot"


def ProcessEncoderAlignment(df: pd.DataFrame) -> Tuple[int, str]:
    """Velocity correlation between TalonFX and CANcoder."""
    talon_vel = _get_numeric(df, talon_key(MOTOR_ID, "Velocity"))
    cc_vel = _get_numeric(df, cancoder_key(CANCODER_ID, "Velocity"))
    if talon_vel.empty or cc_vel.empty:
        return -1, "metric_not_implemented"
    combined = pd.DataFrame({"talon": talon_vel, "cancoder": cc_vel}).interpolate(
        limit_direction="both"
    ).dropna()
    if len(combined) < 10:
        return -1, "insufficient_data"
    moving = combined[combined["talon"].abs() > 0.01]
    if moving.empty:
        return 0, "no movement detected"
    corr = float(moving["talon"].corr(moving["cancoder"]))
    if np.isnan(corr):
        return 0, "no meaningful motion"
    stoplight = 2 if corr < 0.5 else (1 if corr < 0.8 else 0)
    return stoplight, f"r={corr:.3f}"
