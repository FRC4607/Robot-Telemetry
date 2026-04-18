"""
Climber metrics for Power Play (FRC 4607, 2026).
Outer: TalonFX IDs 23 (leader) & 6 (follower, aligned).
Inner: TalonFX IDs 15 (leader) & 56 (follower, aligned).
MotionMagicTorqueCurrentFOC, kMaxAmperage=80, kInchesPerRev=0.19864.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import (
    CLIMBER_OUTER_MOTOR1, CLIMBER_OUTER_MOTOR2,
    CLIMBER_INNER_MOTOR1, CLIMBER_INNER_MOTOR2,
    CLIMBER_MAX_AMPERAGE, talon_key,
)

pd.options.mode.chained_assignment = None

CLIMBER_GROUPS = {
    "Outer": (CLIMBER_OUTER_MOTOR1, CLIMBER_OUTER_MOTOR2),
    "Inner": (CLIMBER_INNER_MOTOR1, CLIMBER_INNER_MOTOR2),
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
    for group_name, (leader_id, follower_id) in CLIMBER_GROUPS.items():
        metrics[f"Climber {group_name} Leader Max Current"] = (
            lambda df, d=leader_id: _max_current(df, d)
        )
        metrics[f"Climber {group_name} Follower Max Current"] = (
            lambda df, d=follower_id: _max_current(df, d)
        )
        metrics[f"Climber {group_name} Leader-Follower Agreement"] = (
            lambda df, l=leader_id, f=follower_id: _follower_agreement(df, l, f)
        )
    return metrics


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
    stoplight = 2 if max_val > CLIMBER_MAX_AMPERAGE else (1 if max_val > CLIMBER_MAX_AMPERAGE * 0.75 else 0)
    return stoplight, f"{max_val:.1f} A"


def _follower_agreement(df: pd.DataFrame, leader_id: int, follower_id: int) -> Tuple[int, str]:
    """Velocity correlation between leader and follower (aligned)."""
    leader_vel = _get_numeric(df, talon_key(leader_id, "Velocity"))
    follower_vel = _get_numeric(df, talon_key(follower_id, "Velocity"))
    if leader_vel.empty or follower_vel.empty:
        return -1, "metric_not_implemented"
    combined = pd.DataFrame({"leader": leader_vel, "follower": follower_vel}).interpolate(
        limit_direction="both"
    ).dropna()
    if len(combined) < 10:
        return -1, "insufficient_data"
    moving = combined[combined["leader"].abs() > 0.01]
    if moving.empty:
        return 0, "no movement detected"
    corr = float(moving["leader"].corr(moving["follower"]))
    if np.isnan(corr):
        return 0, "no meaningful motion"
    stoplight = 2 if corr < 0.5 else (1 if corr < 0.8 else 0)
    return stoplight, f"r={corr:.3f}"
