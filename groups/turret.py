"""
Turret metrics for Slap Shot (FRC 4607, 2026).
TalonFX ID 32 + CANcoder IDs 50 & 51, MotionMagicTorqueCurrentFOC.
RotorToMechanism=10.2, kMaxAmperage=80.
Uses Chinese Remainder Theorem with two encoders for absolute position.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import (
    TURRET_MOTOR, TURRET_ENCODER1, TURRET_ENCODER2, TURRET_MAX_AMPERAGE,
    talon_key, cancoder_key,
)

pd.options.mode.chained_assignment = None

MOTOR_ID = TURRET_MOTOR
ENC1_ID = TURRET_ENCODER1
ENC2_ID = TURRET_ENCODER2


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Turret Max Current": ProcessMaxCurrent,
        "Turret Avg Current": ProcessAvgCurrent,
        "Turret Max Velocity": ProcessMaxVelocity,
        "Turret Position Range": ProcessPositionRange,
        "Turret Encoder1 Alignment": ProcessEncoder1Alignment,
        "Turret Encoder2 Alignment": ProcessEncoder2Alignment,
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
    stoplight = 2 if max_val > TURRET_MAX_AMPERAGE else (1 if max_val > TURRET_MAX_AMPERAGE * 0.75 else 0)
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
    """Report turret motor position range (mechanism rotations)."""
    key = talon_key(MOTOR_ID, "Position")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    min_pos = float(data.min())
    max_pos = float(data.max())
    range_deg = (max_pos - min_pos) * 360.0
    return 0, f"{range_deg:.1f} deg range ({min_pos:.3f} to {max_pos:.3f} rot)"


def _encoder_alignment(df: pd.DataFrame, enc_id: int) -> Tuple[int, str]:
    """Velocity correlation between turret motor and a CANcoder."""
    talon_vel = _get_numeric(df, talon_key(MOTOR_ID, "Velocity"))
    cc_vel = _get_numeric(df, cancoder_key(enc_id, "Velocity"))
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
    stoplight = 2 if abs(corr) < 0.5 else (1 if abs(corr) < 0.8 else 0)
    return stoplight, f"r={corr:.3f}"


def ProcessEncoder1Alignment(df: pd.DataFrame) -> Tuple[int, str]:
    return _encoder_alignment(df, ENC1_ID)


def ProcessEncoder2Alignment(df: pd.DataFrame) -> Tuple[int, str]:
    return _encoder_alignment(df, ENC2_ID)
