"""
Power / supply voltage metrics for Slap Shot (FRC 4607, 2026).
Uses SupplyVoltage and SupplyCurrent from all TalonFX devices.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import ALL_TALON_IDS, talon_key

pd.options.mode.chained_assignment = None


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    subset = df[df["Key"] == key]
    if subset.empty:
        return pd.Series(dtype=float)
    s = pd.to_numeric(subset["Value"], errors="coerce").dropna()
    s.index = subset.index[: len(s)]
    return s


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Bus Starting Voltage": ProcessStartingVoltage,
        "Bus Ending Voltage": ProcessEndingVoltage,
        "Bus Min Voltage": ProcessMinVoltage,
        "Bus Max Total Supply Current": ProcessMaxTotalCurrent,
    }


def _get_all_supply_voltages(df: pd.DataFrame) -> pd.Series:
    """Get supply voltage from the first TalonFX that has data."""
    for tid in sorted(ALL_TALON_IDS):
        data = _get_numeric(df, talon_key(tid, "SupplyVoltage"))
        if not data.empty:
            return data
    return pd.Series(dtype=float)


def ProcessStartingVoltage(df: pd.DataFrame) -> Tuple[int, str]:
    data = _get_all_supply_voltages(df)
    if data.empty:
        return -1, "metric_not_implemented"
    # Average of first 50 samples
    start_v = float(data.iloc[: min(50, len(data))].mean())
    stoplight = 2 if start_v < 12.0 else (1 if start_v < 12.15 else 0)
    return stoplight, f"{start_v:.2f} V"


def ProcessEndingVoltage(df: pd.DataFrame) -> Tuple[int, str]:
    data = _get_all_supply_voltages(df)
    if data.empty:
        return -1, "metric_not_implemented"
    # Average of last 50 samples
    end_v = float(data.iloc[-min(50, len(data)) :].mean())
    stoplight = 2 if end_v < 11.0 else (1 if end_v < 11.2 else 0)
    return stoplight, f"{end_v:.2f} V"


def ProcessMinVoltage(df: pd.DataFrame) -> Tuple[int, str]:
    data = _get_all_supply_voltages(df)
    if data.empty:
        return -1, "metric_not_implemented"
    window = min(10, len(data))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(data.to_numpy(), np.ones(window) / window, "valid")
    min_v = float(smoothed.min())
    stoplight = 2 if min_v < 7.0 else (1 if min_v < 8.5 else 0)
    return stoplight, f"{min_v:.2f} V"


def ProcessMaxTotalCurrent(df: pd.DataFrame) -> Tuple[int, str]:
    """Sum of SupplyCurrent across all known TalonFX devices, report max."""
    all_currents = []
    for tid in sorted(ALL_TALON_IDS):
        data = _get_numeric(df, talon_key(tid, "SupplyCurrent"))
        if not data.empty:
            all_currents.append(data.rename(f"talon_{tid}"))

    if not all_currents:
        return -1, "metric_not_implemented"

    combined = pd.concat(all_currents, axis=1).interpolate(limit_direction="both").fillna(0)
    total = combined.sum(axis=1)

    window = min(50, len(total))
    if window < 2:
        return -1, "insufficient_data"
    smoothed = np.convolve(total.to_numpy(), np.ones(window) / window, "valid")
    max_val = float(smoothed.max())

    stoplight = 2 if max_val > 200 else (1 if max_val > 150 else 0)
    return stoplight, f"{max_val:.1f} A"
