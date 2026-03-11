"""
Flywheel metrics for Slap Shot (FRC 4607, 2026).
TalonFX IDs 50 (leader) & 51 (follower, opposed), VelocityTorqueCurrentFOC.
kMaxAmperage=80. Shot calibrations: HubShot=45 rot/s, Depot=56, Outpost=58/73.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import (
    FLYWHEEL_MOTOR1, FLYWHEEL_MOTOR2, FLYWHEEL_MAX_AMPERAGE, talon_key,
)

pd.options.mode.chained_assignment = None

LEADER_ID = FLYWHEEL_MOTOR1
FOLLOWER_ID = FLYWHEEL_MOTOR2


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Flywheel Leader Max Current": ProcessLeaderMaxCurrent,
        "Flywheel Leader Avg Current": ProcessLeaderAvgCurrent,
        "Flywheel Follower Max Current": ProcessFollowerMaxCurrent,
        "Flywheel Max Velocity": ProcessMaxVelocity,
        "Flywheel Leader-Follower Agreement": ProcessFollowerAgreement,
    }


def _max_current(df: pd.DataFrame, device_id: int) -> Tuple[int, str]:
    key = talon_key(device_id, "StatorCurrent")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(50, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    max_val = float(smoothed.max())
    stoplight = 2 if max_val > FLYWHEEL_MAX_AMPERAGE else (1 if max_val > FLYWHEEL_MAX_AMPERAGE * 0.75 else 0)
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


def ProcessLeaderMaxCurrent(df: pd.DataFrame) -> Tuple[int, str]:
    return _max_current(df, LEADER_ID)


def ProcessLeaderAvgCurrent(df: pd.DataFrame) -> Tuple[int, str]:
    return _avg_current(df, LEADER_ID)


def ProcessFollowerMaxCurrent(df: pd.DataFrame) -> Tuple[int, str]:
    return _max_current(df, FOLLOWER_ID)


def ProcessMaxVelocity(df: pd.DataFrame) -> Tuple[int, str]:
    key = talon_key(LEADER_ID, "Velocity")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"
    max_val = float(data.abs().max())
    return 0, f"{max_val:.1f} rot/s"


def ProcessFollowerAgreement(df: pd.DataFrame) -> Tuple[int, str]:
    """Velocity correlation between leader and follower flywheel motors."""
    leader_vel = _get_numeric(df, talon_key(LEADER_ID, "Velocity"))
    follower_vel = _get_numeric(df, talon_key(FOLLOWER_ID, "Velocity"))
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
    # Follower is opposed, so expect negative correlation
    corr = float(moving["leader"].corr(moving["follower"]))
    if np.isnan(corr):
        return 0, "no meaningful motion"
    # Opposed follower: expect corr near -1.0
    stoplight = 2 if abs(corr) < 0.5 else (1 if abs(corr) < 0.8 else 0)
    return stoplight, f"r={corr:.3f} (opposed)"
