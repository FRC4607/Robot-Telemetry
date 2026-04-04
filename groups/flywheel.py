"""
Flywheel metrics for FRC 4607, 2026 (second robot).
Left Flywheel: TalonFX IDs 4 (leader) & 5 (follower, opposed).
Right Flywheel: TalonFX IDs 19 (leader) & 18 (follower, opposed).
VelocityTorqueCurrentFOC, kMaxAmperage=80.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import (
    LEFT_FLYWHEEL_MOTOR1, LEFT_FLYWHEEL_MOTOR2, LEFT_FLYWHEEL_MAX_AMPERAGE,
    RIGHT_FLYWHEEL_MOTOR1, RIGHT_FLYWHEEL_MOTOR2, RIGHT_FLYWHEEL_MAX_AMPERAGE,
    talon_key,
)

pd.options.mode.chained_assignment = None

FLYWHEELS = {
    "Left": {
        "leader": LEFT_FLYWHEEL_MOTOR1,
        "follower": LEFT_FLYWHEEL_MOTOR2,
        "max_amp": LEFT_FLYWHEEL_MAX_AMPERAGE,
    },
    "Right": {
        "leader": RIGHT_FLYWHEEL_MOTOR1,
        "follower": RIGHT_FLYWHEEL_MOTOR2,
        "max_amp": RIGHT_FLYWHEEL_MAX_AMPERAGE,
    },
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
    for side, info in FLYWHEELS.items():
        leader_id = info["leader"]
        follower_id = info["follower"]
        max_amp = info["max_amp"]
        metrics[f"{side} Flywheel Leader Max Current"] = (
            lambda df, d=leader_id, m=max_amp: _max_current(df, d, m)
        )
        metrics[f"{side} Flywheel Leader Avg Current"] = (
            lambda df, d=leader_id: _avg_current(df, d)
        )
        metrics[f"{side} Flywheel Follower Max Current"] = (
            lambda df, d=follower_id, m=max_amp: _max_current(df, d, m)
        )
        metrics[f"{side} Flywheel Max Velocity"] = (
            lambda df, d=leader_id: _max_velocity(df, d)
        )
        metrics[f"{side} Flywheel Leader-Follower Agreement"] = (
            lambda df, l=leader_id, f=follower_id: _follower_agreement(df, l, f)
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
    stoplight = 2 if avg_val > 30 else (1 if avg_val > 15 else 0)
    return stoplight, f"{avg_val:.1f} A"


def _max_velocity(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    key = talon_key(device_id, "Velocity")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    max_val = float(data.abs().max())
    return 0, f"{max_val:.1f} rot/s"


def _follower_agreement(df: pd.DataFrame, leader_id: int, follower_id: int) -> Tuple[int, str]:
    leader_vel = _get_numeric(df, talon_key(leader_id, "Velocity"))
    follower_vel = _get_numeric(df, talon_key(follower_id, "Velocity"))
    if leader_vel.empty or follower_vel.empty:
        return -1, "metric_not_implemented"
    combined = pd.DataFrame({"leader": leader_vel, "follower": follower_vel}).interpolate(
        limit_direction="both"
    ).dropna()
    if len(combined) < 10:
        return -1, "insufficient_data"
    moving = combined[combined["leader"].abs() > 0.5]
    if moving.empty:
        return 0, "no movement detected"
    corr = float(moving["leader"].corr(moving["follower"]))
    if np.isnan(corr):
        return 0, "no meaningful motion"
    # Follower is opposed, so expect negative correlation
    stoplight = 2 if abs(corr) < 0.5 else (1 if abs(corr) < 0.8 else 0)
    return stoplight, f"r={corr:.3f} (opposed)"
