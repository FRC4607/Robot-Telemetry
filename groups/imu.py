"""
Pigeon2 IMU metrics for Phoenix6 / hoot log data.
Analyzes yaw drift and angular velocity from the Pigeon2 gyro.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
from scipy import stats
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import pigeon_key
from config.metric_thresholds import get_threshold, high_is_bad
from metric_cache import get_numeric_cached

pd.options.mode.chained_assignment = None


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    return get_numeric_cached(df, key)


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Pigeon Yaw Drift": ProcessYawDrift,
        "Pigeon Max Angular Velocity": ProcessMaxAngularVelocity,
    }


def ProcessYawNormality(df: pd.DataFrame) -> Tuple[int, str]:
    """Shapiro-Wilk normality test on yaw rate of change (checks for erratic IMU behavior)."""
    key = pigeon_key("AngularVelocityZWorld")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"

    # Subsample if too large for Shapiro-Wilk (max 5000)
    sample = data.sample(n=min(5000, len(data)), random_state=42) if len(data) > 5000 else data
    stat, p_value = stats.shapiro(sample.to_numpy())
    stoplight = 0 if p_value > 0.05 else (1 if p_value > 0.01 else 2)
    return stoplight, f"p={p_value:.4f} (W={stat:.4f})"


def ProcessYawDrift(df: pd.DataFrame) -> Tuple[int, str]:
    """Estimate yaw drift by checking angular velocity when robot is mostly stationary.
    Looks at periods where angular velocity is very low to detect gyro bias."""
    yaw_rate_key = pigeon_key("AngularVelocityZWorld")
    yaw_rate = _get_numeric(df, yaw_rate_key)
    if yaw_rate.empty or len(yaw_rate) < 100:
        return -1, "metric_not_implemented"

    # Filter to "stationary" periods: angular velocity < 2 deg/s
    stationary = yaw_rate[yaw_rate.abs() < 2.0]
    if len(stationary) < 50:
        return 0, "insufficient stationary data"

    # The mean angular velocity during stationary periods is the drift bias
    drift_bias = float(stationary.mean())
    drift_per_min = abs(drift_bias) * 60  # deg/s -> deg/min

    warning = float(get_threshold("imu.yaw_drift_deg_per_min.warning", 1.0))
    critical = float(get_threshold("imu.yaw_drift_deg_per_min.critical", 2.0))
    stoplight = high_is_bad(drift_per_min, warning, critical)
    return stoplight, f"{drift_per_min:.3f} deg/min (bias={drift_bias:.4f} deg/s)"


def ProcessMaxAngularVelocity(df: pd.DataFrame) -> Tuple[int, str]:
    """Report maximum angular velocity seen (deg/s)."""
    key = pigeon_key("AngularVelocityZWorld")
    data = _get_numeric(df, key)
    if data.empty:
        return -1, "metric_not_implemented"

    max_val = float(data.abs().max())
    return 0, f"{max_val:.1f} deg/s"
