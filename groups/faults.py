"""
Device Fault metrics for FRC 4607, 2026 (second robot).
Scans all Phoenix6 device Fault_* signals and reports any that fired.
Covers brownouts, hardware faults, overtemperature, remote sensor issues, etc.
"""

from typing import Callable, Dict, Tuple
import pandas as pd
import numpy as np
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.device_map import ALL_TALON_IDS, ALL_CANCODER_IDS, PIGEON_ID, talon_key, cancoder_key, pigeon_key
from config.metric_thresholds import get_threshold, high_is_bad
from metric_cache import get_numeric_cached

pd.options.mode.chained_assignment = None

# Faults that matter for stoplight judgments
# (signal_suffix, yellow_threshold_pct, red_threshold_pct)
# Thresholds are the percentage of samples where the fault is active.
CRITICAL_FAULTS = {
    "Fault_BridgeBrownout": ("Brownout", 1.0, 5.0),
    "Fault_Hardware": ("Hardware Fault", 0.1, 1.0),
    "Fault_DeviceTemp": ("Overtemp", 1.0, 5.0),
    "Fault_RemoteSensorDataInvalid": ("Remote Sensor Invalid", 10.0, 40.0),
    "Fault_Undervoltage": ("Undervoltage", 1.0, 5.0),
}

# Friendly names for devices
_DEVICE_NAMES = {}
_SWERVE_NAMES = {23: "FL Drive", 22: "FL Steer", 0: "FR Drive", 1: "FR Steer",
                 2: "BL Drive", 3: "BL Steer", 21: "BR Drive", 20: "BR Steer"}
_MECH_NAMES = {15: "Intake Arm", 14: "Intake Wheels", 13: "Indexer",
               6: "L Chamber", 17: "R Chamber", 7: "L Turret", 16: "R Turret",
               8: "L Hood", 9: "R Hood", 4: "L Fly1", 5: "L Fly2",
               19: "R Fly1", 18: "R Fly2"}
_DEVICE_NAMES.update(_SWERVE_NAMES)
_DEVICE_NAMES.update(_MECH_NAMES)


def _get_numeric(df: pd.DataFrame, key: str) -> pd.Series:
    return get_numeric_cached(df, key)


def defineMetrics() -> Dict[str, Callable[[pd.DataFrame], Tuple[int, str]]]:
    return {
        "Brownout Count": _brownout_count,
        "Brownout Devices": _brownout_devices,
        "Hardware Faults": _hardware_faults,
        "Overtemp Faults": _overtemp_faults,
        "Remote Sensor Faults": _remote_sensor_faults,
    }


def _check_fault_across_talons(df: pd.DataFrame, fault_signal: str) -> Dict[int, float]:
    """Returns {device_id: percentage_of_samples_faulted} for all TalonFX devices."""
    result = {}
    for tid in sorted(ALL_TALON_IDS):
        key = talon_key(tid, fault_signal)
        data = _get_numeric(df, key)
        if data.empty:
            continue
        pct = float((data != 0).sum()) / len(data) * 100
        if pct > 0:
            result[tid] = pct
    return result


def _brownout_count(df: pd.DataFrame) -> Tuple[int, str]:
    """Count total brownout events across all TalonFX devices."""
    faulted = _check_fault_across_talons(df, "Fault_BridgeBrownout")
    if not faulted:
        # Check if we have any data at all
        for tid in sorted(ALL_TALON_IDS):
            key = talon_key(tid, "Fault_BridgeBrownout")
            if not _get_numeric(df, key).empty:
                return 0, "0 brownouts"
        return -1, "metric_not_implemented"
    total_devices = len(faulted)
    max_pct = max(faulted.values())
    warning = float(get_threshold("faults.brownout_count_pct.warning", 5.0))
    critical = float(get_threshold("faults.brownout_count_pct.critical", 15.0))
    stoplight = high_is_bad(max_pct, warning, critical)
    return stoplight, f"{total_devices} device(s), worst {max_pct:.1f}%"


def _brownout_devices(df: pd.DataFrame) -> Tuple[int, str]:
    """List which devices experienced brownouts."""
    faulted = _check_fault_across_talons(df, "Fault_BridgeBrownout")
    if not faulted:
        return 0, "none"
    names = [_DEVICE_NAMES.get(tid, f"TalonFX-{tid}") for tid in sorted(faulted.keys())]
    warning = int(get_threshold("faults.brownout_devices_count.warning", 10))
    critical = int(get_threshold("faults.brownout_devices_count.critical", 14))
    stoplight = high_is_bad(float(len(names)), float(warning), float(critical))
    return stoplight, ", ".join(names)


def _hardware_faults(df: pd.DataFrame) -> Tuple[int, str]:
    """Check for hardware faults on any device."""
    faulted = _check_fault_across_talons(df, "Fault_Hardware")
    if not faulted:
        return 0, "none"
    names = [_DEVICE_NAMES.get(tid, f"TalonFX-{tid}") for tid in sorted(faulted.keys())]
    return 2, ", ".join(f"{n} ({faulted[tid]:.1f}%)" for tid, n in zip(sorted(faulted.keys()), names))


def _overtemp_faults(df: pd.DataFrame) -> Tuple[int, str]:
    """Check for overtemperature faults on any device."""
    faulted = _check_fault_across_talons(df, "Fault_DeviceTemp")
    if not faulted:
        return 0, "none"
    names = [_DEVICE_NAMES.get(tid, f"TalonFX-{tid}") for tid in sorted(faulted.keys())]
    max_pct = max(faulted.values())
    critical = float(get_threshold("faults.overtemp_fault_pct.critical", 5.0))
    stoplight = 2 if max_pct > critical else 1
    return stoplight, ", ".join(f"{n} ({faulted[tid]:.1f}%)" for tid, n in zip(sorted(faulted.keys()), names))


def _remote_sensor_faults(df: pd.DataFrame) -> Tuple[int, str]:
    """Check for RemoteSensorDataInvalid faults (indicates CANcoder communication issues)."""
    faulted = _check_fault_across_talons(df, "Fault_RemoteSensorDataInvalid")
    if not faulted:
        return 0, "none"
    names = [_DEVICE_NAMES.get(tid, f"TalonFX-{tid}") for tid in sorted(faulted.keys())]
    max_pct = max(faulted.values())
    warning = float(get_threshold("faults.remote_sensor_fault_pct.warning", 45.0))
    critical = float(get_threshold("faults.remote_sensor_fault_pct.critical", 60.0))
    stoplight = high_is_bad(max_pct, warning, critical)
    return stoplight, ", ".join(f"{n} ({faulted[tid]:.1f}%)" for tid, n in zip(sorted(faulted.keys()), names))
