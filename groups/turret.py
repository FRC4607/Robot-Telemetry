"""
Turret metrics for FRC 4607, 2026 (second robot).
Left Turret: TalonFX ID 7 + CANcoder IDs 31 & 32.
Right Turret: TalonFX ID 16 + CANcoder IDs 41 & 42.
MotionMagicTorqueCurrentFOC, RotorToMechanism=10.2, kMaxAmperage=80.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import (
    LEFT_TURRET_MOTOR, LEFT_TURRET_ENCODER1, LEFT_TURRET_ENCODER2, LEFT_TURRET_MAX_AMPERAGE,
    RIGHT_TURRET_MOTOR, RIGHT_TURRET_ENCODER1, RIGHT_TURRET_ENCODER2, RIGHT_TURRET_MAX_AMPERAGE,
    talon_key, cancoder_key,
)
from metric_cache import get_numeric_cached

pd.options.mode.chained_assignment = None

TURRETS = {
    "Left": {
        "motor": LEFT_TURRET_MOTOR,
        "enc1": LEFT_TURRET_ENCODER1,
        "enc2": LEFT_TURRET_ENCODER2,
        "max_amp": LEFT_TURRET_MAX_AMPERAGE,
    },
    "Right": {
        "motor": RIGHT_TURRET_MOTOR,
        "enc1": RIGHT_TURRET_ENCODER1,
        "enc2": RIGHT_TURRET_ENCODER2,
        "max_amp": RIGHT_TURRET_MAX_AMPERAGE,
    },
}


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    return get_numeric_cached(df, key)


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    metrics = {}
    for side, info in TURRETS.items():
        motor_id = info["motor"]
        enc1_id = info["enc1"]
        enc2_id = info["enc2"]
        max_amp = info["max_amp"]
        metrics[f"{side} Turret Max Current"] = (
            lambda df, d=motor_id, m=max_amp: _max_current(df, d, m)
        )
        metrics[f"{side} Turret Avg Current"] = (
            lambda df, d=motor_id: _avg_current(df, d)
        )
        metrics[f"{side} Turret Max Velocity"] = (
            lambda df, d=motor_id: _max_velocity(df, d)
        )
        metrics[f"{side} Turret Position Range"] = (
            lambda df, d=motor_id: _position_range(df, d)
        )
        metrics[f"{side} Turret Position Error"] = (
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
    stoplight = 2 if avg_val > 50 else (1 if avg_val > 25 else 0)
    return stoplight, f"{avg_val:.1f} A"


def _max_velocity(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    key = talon_key(device_id, "Velocity")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    max_val = float(data.abs().max())
    return 0, f"{max_val:.1f} rot/s"


def _position_range(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    key = talon_key(device_id, "Position")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    min_pos = float(data.min())
    max_pos = float(data.max())
    range_deg = (max_pos - min_pos) * 360.0
    return 0, f"{range_deg:.1f} deg range ({min_pos:.3f} to {max_pos:.3f} rot)"


def _position_error(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    """Mean |ClosedLoopError| when the turret is actively moving."""
    err = _get_numeric(df, talon_key(device_id, "ClosedLoopError"))
    ref = _get_numeric(df, talon_key(device_id, "ClosedLoopReference"))
    if err.empty or ref.empty:
        return -1, "no data (needs licensed owlet)"
    combined = pd.DataFrame({"err": err, "ref": ref}).interpolate(limit_direction="both").dropna()
    # Filter to when reference is changing (turret in motion)
    ref_diff = combined["ref"].diff().abs()
    active = combined[ref_diff > 0.0001]  # reference changing
    if len(active) < 10:
        # Fall back to all samples where error is nonzero
        active = combined[combined["err"].abs() > 0.0001]
    if len(active) < 10:
        return 0, "no active positioning detected"
    mean_err = float(active["err"].abs().mean())
    peak_err = float(active["err"].abs().max())
    mean_deg = mean_err * 360.0
    peak_deg = peak_err * 360.0
    stoplight = 2 if mean_deg > 5.0 else (1 if mean_deg > 2.0 else 0)
    return stoplight, f"avg {mean_deg:.2f} deg, peak {peak_deg:.1f} deg"


def _encoder_alignment(df: pd.DataFrame, motor_id: int, enc_id: int) -> Tuple[int, str]:
    talon_vel = _get_numeric(df, talon_key(motor_id, "Velocity"))
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
