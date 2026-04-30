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
from config.metric_thresholds import get_threshold, high_is_bad
from metric_cache import get_numeric_cached

pd.options.mode.chained_assignment = None

MOTOR_ID = INTAKE_ARM_MOTOR
CANCODER_ID = INTAKE_ARM_CANCODER


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    return get_numeric_cached(df, key)


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Intake Arm Max Current": ProcessMaxCurrent,
        "Intake Arm Avg Current": ProcessAvgCurrent,
        "Intake Arm Max Velocity": ProcessMaxVelocity,
        "Intake Arm Position Range": ProcessPositionRange,
        "Intake Arm Position Error": ProcessPositionError,
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
    stoplight = high_is_bad(max_val, INTAKE_ARM_MAX_AMPERAGE * warn_ratio, INTAKE_ARM_MAX_AMPERAGE)
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
    warning = float(get_threshold("intake_arm.avg_current.warning", 25.0))
    critical = float(get_threshold("intake_arm.avg_current.critical", 35.0))
    stoplight = high_is_bad(avg_val, warning, critical)
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


def ProcessPositionError(df: pd.DataFrame) -> Tuple[int, str]:
    """Mean |ClosedLoopError| when the intake arm is actively positioning."""
    err = _get_numeric(df, talon_key(MOTOR_ID, "ClosedLoopError"))
    ref = _get_numeric(df, talon_key(MOTOR_ID, "ClosedLoopReference"))
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
    warning = float(get_threshold("intake_arm.position_error_deg.warning", 2.0))
    critical = float(get_threshold("intake_arm.position_error_deg.critical", 5.0))
    stoplight = high_is_bad(mean_deg, warning, critical)
    return stoplight, f"avg {mean_deg:.2f} deg, peak {peak_deg:.1f} deg"


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
    warning = float(get_threshold("intake_arm.encoder_alignment_corr.warning", 0.8))
    critical = float(get_threshold("intake_arm.encoder_alignment_corr.critical", 0.5))
    stoplight = 2 if corr < critical else (1 if corr < warning else 0)
    return stoplight, f"r={corr:.3f}"
