"""
Swerve drive metrics for Slap Shot (FRC 4607, 2026).
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

pd.options.mode.chained_assignment = None

# Drive slip current from TunerConstants; steer current limit is 60A (steer config)
CURRENT_THRESHOLDS = {
    "drive": {"max": (80, SWERVE_SLIP_CURRENT), "avg": (25, 50)},
    "steer": {"max": (30, 60), "avg": (5, 20)},
}


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


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
        metrics[f"{abbrev} Encoder Alignment"] = (
            lambda df, s=steer_id, c=cc_id: _encoder_alignment(df, s, c)
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
    stoplight = 2 if max_val > red else (1 if max_val > yellow else 0)
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
        combined = combined[combined["volt"].abs() > 0.5]
        if combined.empty:
            return 0, "0.0 A (motor inactive)"
        avg_val = float(combined["curr"].mean())
    else:
        avg_val = float(currents.mean())
    yellow, red = CURRENT_THRESHOLDS[role]["avg"]
    stoplight = 2 if avg_val > red else (1 if avg_val > yellow else 0)
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
    stoplight = 2 if min_val < 7.0 else (1 if min_val < 8.5 else 0)
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
    stoplight = 2 if corr < 0.5 else (1 if corr < 0.8 else 0)
    return stoplight, f"r={corr:.3f}"
