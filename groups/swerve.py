"""
Swerve drive metrics for FRC 4607, 2026 (second robot).
Analyzes each module's drive and steer TalonFX motors and CANcoders.
TunerConstants: DriveGearRatio=6.027, SteerGearRatio=26.09, kSlipCurrent=120A
All motors use TorqueCurrentFOC control.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import (
    SWERVE_MODULES, SWERVE_SLIP_CURRENT, talon_key, cancoder_key,
)
from config.metric_thresholds import get_threshold, high_is_bad, low_is_bad
from metric_cache import get_numeric_cached

pd.options.mode.chained_assignment = None

CURRENT_THRESHOLDS = {
    "drive": {
        "max": (
            float(get_threshold("swerve.drive.max_current.warning", SWERVE_SLIP_CURRENT)),
            float(get_threshold("swerve.drive.max_current.critical", 130.0)),
        ),
        "avg": (
            float(get_threshold("swerve.drive.avg_current.warning", 45.0)),
            float(get_threshold("swerve.drive.avg_current.critical", 60.0)),
        ),
    },
    "steer": {
        "max": (
            float(get_threshold("swerve.steer.max_current.warning", 40.0)),
            float(get_threshold("swerve.steer.max_current.critical", 60.0)),
        ),
        "avg": (
            float(get_threshold("swerve.steer.avg_current.warning", 10.0)),
            float(get_threshold("swerve.steer.avg_current.critical", 20.0)),
        ),
    },
}


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    return get_numeric_cached(df, key)


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    metrics = {}
    for name, ids in SWERVE_MODULES.items():
        abbrev = "".join(w[0] for w in name.split())  # FL, FR, BL, BR
        drive_id = ids["drive"]
        steer_id = ids["steer"]
        cc_id = ids["cancoder"]

        metrics[f"{abbrev} Drive Max Current"] = (
            lambda df, d=drive_id: _max_current(df, d, "drive")
        )
        metrics[f"{abbrev} Drive Avg Current"] = (
            lambda df, d=drive_id: _avg_current(df, d, "drive")
        )
        metrics[f"{abbrev} Steer Max Current"] = (
            lambda df, s=steer_id: _max_current(df, s, "steer")
        )
        metrics[f"{abbrev} Steer Avg Current"] = (
            lambda df, s=steer_id: _avg_current(df, s, "steer")
        )
        metrics[f"{abbrev} Min Supply Voltage"] = (
            lambda df, d=drive_id: _min_supply_voltage(df, d)
        )
        metrics[f"{abbrev} Drive Max Velocity"] = (
            lambda df, d=drive_id: _max_velocity(df, d)
        )
        metrics[f"{abbrev} Steer Position Error"] = (
            lambda df, s=steer_id: _steer_position_error(df, s)
        )
    return metrics


def _max_current(df: pd.DataFrame, device_id: int, role: str) -> Tuple[int, str]:
    key = talon_key(device_id, "StatorCurrent")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(50, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    max_val = float(smoothed.max())
    yellow, red = CURRENT_THRESHOLDS[role]["max"]
    stoplight = high_is_bad(max_val, yellow, red)
    return stoplight, f"{max_val:.1f} A"


def _avg_current(df: pd.DataFrame, device_id: int, role: str) -> Tuple[int, str]:
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
    yellow, red = CURRENT_THRESHOLDS[role]["avg"]
    stoplight = high_is_bad(avg_val, yellow, red)
    return stoplight, f"{avg_val:.1f} A"


def _min_supply_voltage(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    key = talon_key(device_id, "SupplyVoltage")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(10, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    min_val = float(smoothed.min())
    warning = float(get_threshold("swerve.min_supply_voltage.warning", 6.5))
    critical = float(get_threshold("swerve.min_supply_voltage.critical", 6.0))
    stoplight = low_is_bad(min_val, warning, critical)
    return stoplight, f"{min_val:.2f} V"


def _max_velocity(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    key = talon_key(device_id, "Velocity")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    max_val = float(data.abs().max())
    return 0, f"{max_val:.1f} rot/s"


def _encoder_alignment(
    df: pd.DataFrame, steer_talon_id: int, cancoder_id: int
) -> Tuple[int, str]:
    """Velocity correlation between steer TalonFX and CANcoder."""
    talon_vel = _get_numeric(df, talon_key(steer_talon_id, "Velocity"))
    cc_vel = _get_numeric(df, cancoder_key(cancoder_id, "Velocity"))
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
    warning = float(get_threshold("swerve.encoder_alignment_corr.warning", 0.8))
    critical = float(get_threshold("swerve.encoder_alignment_corr.critical", 0.5))
    stoplight = 2 if corr < critical else (1 if corr < warning else 0)
    return stoplight, f"r={corr:.3f}"


def _steer_position_error(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    """Mean |ClosedLoopError| for swerve steer positioning."""
    err = _get_numeric(df, talon_key(device_id, "ClosedLoopError"))
    ref = _get_numeric(df, talon_key(device_id, "ClosedLoopReference"))
    if err.empty or ref.empty:
        return -1, "no data (needs licensed owlet)"
    combined = pd.DataFrame({"err": err, "ref": ref}).interpolate(limit_direction="both").dropna()
    ref_diff = combined["ref"].diff().abs()
    active = combined[ref_diff > 0.0001]
    if len(active) < 10:
        active = combined[combined["err"].abs() > 0.0001]
    if len(active) < 10:
        return 0, "no active steering detected"
    mean_err = float(active["err"].abs().mean())
    peak_err = float(active["err"].abs().max())
    mean_deg = mean_err * 360.0
    peak_deg = peak_err * 360.0
    warning = float(get_threshold("swerve.steer_position_error_deg.warning", 1.0))
    critical = float(get_threshold("swerve.steer_position_error_deg.critical", 3.0))
    stoplight = high_is_bad(mean_deg, warning, critical)
    return stoplight, f"avg {mean_deg:.2f} deg, peak {peak_deg:.1f} deg"
