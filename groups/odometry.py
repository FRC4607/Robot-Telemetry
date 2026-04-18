"""
Odometry / drivetrain state metrics for FRC 4607, 2026 (second robot).
Uses DriveState/Pose, DriveState/ModuleStates, DriveState/ModuleTargets,
and DriveState/OdometryPeriod from the Telemetry class output.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pd.options.mode.chained_assignment = None


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def _get_array(df: pd.DataFrame, key: str) -> list:
    """Get rows whose Value is an ndarray (DriveState signals)."""
    subset = df[df["Key"] == key]
    if subset.empty:
        return []
    return [v for v in subset["Value"] if isinstance(v, np.ndarray)]


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Total Distance": _total_distance,
        "Max Robot Speed": _max_robot_speed,
        "Odo Update Rate": _odo_update_rate,
        "Swerve Tracking Error": _swerve_tracking_error,
    }


def _total_distance(df: pd.DataFrame) -> Tuple[int, str]:
    """Total distance traveled on the field from odometry pose (meters)."""
    poses = _get_array(df, "DriveState/Pose")
    if len(poses) < 2:
        return -1, "metric_not_implemented"

    total = 0.0
    prev = poses[0]
    for pose in poses[1:]:
        if len(pose) >= 2 and len(prev) >= 2:
            dx = pose[0] - prev[0]
            dy = pose[1] - prev[1]
            total += np.sqrt(dx * dx + dy * dy)
        prev = pose

    # A normal match has roughly 20-80m of movement
    stoplight = 1 if total < 5.0 else 0
    return stoplight, f"{total:.1f} m"


def _max_robot_speed(df: pd.DataFrame) -> Tuple[int, str]:
    """Maximum instantaneous robot speed on the field (m/s) from pose deltas."""
    poses = _get_array(df, "DriveState/Pose")
    if len(poses) < 10:
        return -1, "metric_not_implemented"

    # Estimate speed from consecutive poses
    # Pose is logged at ~250 Hz (OdometryPeriod ~4ms)
    speeds = []
    for i in range(1, len(poses)):
        if len(poses[i]) >= 2 and len(poses[i - 1]) >= 2:
            dx = poses[i][0] - poses[i - 1][0]
            dy = poses[i][1] - poses[i - 1][1]
            dist = np.sqrt(dx * dx + dy * dy)
            # Assume ~4ms between samples (250 Hz)
            speed = dist / 0.004
            speeds.append(speed)

    if not speeds:
        return -1, "insufficient_data"

    # Smooth to avoid noise spikes
    arr = np.array(speeds)
    window = min(50, len(arr))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(arr, np.ones(window) / window, "valid")
    max_speed = float(smoothed.max())

    return 0, f"{max_speed:.2f} m/s"


def _odo_update_rate(df: pd.DataFrame) -> Tuple[int, str]:
    """Average and worst-case odometry update period (ms)."""
    data = _get_numeric(df, "DriveState/OdometryPeriod")
    if data.empty or len(data) < 10:
        return -1, "metric_not_implemented"

    avg_ms = float(data.mean()) * 1000
    max_ms = float(data.max()) * 1000
    avg_hz = 1000.0 / avg_ms if avg_ms > 0 else 0

    # Nominal is 250 Hz (4ms). Yellow if worst case exceeds 10ms, red if >20ms
    stoplight = 2 if max_ms > 20 else (1 if max_ms > 10 else 0)
    return stoplight, f"{avg_hz:.0f} Hz avg, {max_ms:.1f} ms worst"


def _swerve_tracking_error(df: pd.DataFrame) -> Tuple[int, str]:
    """RMS error between ModuleStates (actual) and ModuleTargets (commanded).

    Each array is [angle0, speed0, angle1, speed1, angle2, speed2, angle3, speed3]
    in radians and m/s. We compute speed tracking error as the RMS of
    (actual_speed - target_speed) across all 4 modules during active driving.
    """
    states = _get_array(df, "DriveState/ModuleStates")
    targets = _get_array(df, "DriveState/ModuleTargets")
    if len(states) < 10 or len(targets) < 10:
        return -1, "metric_not_implemented"

    n = min(len(states), len(targets))
    speed_errors = []
    for i in range(n):
        s, t = states[i], targets[i]
        if len(s) < 8 or len(t) < 8:
            continue
        # Extract speed values (indices 1, 3, 5, 7)
        for j in [1, 3, 5, 7]:
            target_speed = t[j]
            actual_speed = s[j]
            # Only count when the robot is actually commanded to move
            if abs(target_speed) > 0.1:
                speed_errors.append((actual_speed - target_speed) ** 2)

    if not speed_errors:
        return 0, "no commanded motion"

    rms = float(np.sqrt(np.mean(speed_errors)))
    # Threshold: <0.2 m/s RMS is great, >0.5 is concerning, >1.0 is bad
    stoplight = 2 if rms > 1.0 else (1 if rms > 0.5 else 0)
    return stoplight, f"{rms:.3f} m/s RMS"
