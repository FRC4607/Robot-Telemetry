"""
InfluxDB writer for raw robot telemetry data.

Writes all numeric time-series data from wpilog files into InfluxDB 2.x,
enabling detailed time-series analysis and Grafana visualization.

Data model:
    Measurement: "robot_telemetry"
    Tags: file, device_type, device_id, signal, event_key, match_info
    Fields: value (float64)
    Timestamp: wall-clock microseconds (log start time + wpilog offset)
"""

import os
import re
import logging
import datetime
from typing import Optional, Set

import pandas as pd
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from keys import INFLUX_TOKEN as _DEFAULT_TOKEN

log = logging.getLogger("telemetry")

# ── Connection settings (overridable via environment variables) ─────────────
INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", _DEFAULT_TOKEN)
INFLUX_ORG = os.environ.get("INFLUX_ORG", "frc4607")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "robot-telemetry")

BATCH_SIZE = 5000

# Parse Phoenix6 signal keys: "Phoenix6/TalonFX-1/StatorCurrent"
_KEY_PATTERN = re.compile(r"^Phoenix6/(\w+)-(\d+)/(.+)$")

# Module-level cache of already-uploaded filenames (lazy-initialized).
_uploaded_files: Optional[Set[str]] = None


def _get_client() -> InfluxDBClient:
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)


# ── Deduplication ──────────────────────────────────────────────────────────────

def _load_uploaded_cache() -> Set[str]:
    """Query InfluxDB once for all filenames that have already been uploaded."""
    try:
        with _get_client() as client:
            query = f'''
            from(bucket: "{INFLUX_BUCKET}")
                |> range(start: 0)
                |> filter(fn: (r) => r._measurement == "_upload_tracking")
                |> filter(fn: (r) => r._field == "uploaded")
                |> keep(columns: ["file"])
                |> distinct(column: "file")
            '''
            result = client.query_api().query(query)
            files: Set[str] = set()
            for table in result:
                for record in table.records:
                    f = record.values.get("file", "")
                    if f:
                        files.add(f)
            log.info("InfluxDB: %d files already uploaded", len(files))
            return files
    except Exception as e:
        log.warning("InfluxDB: failed to load uploaded-file list: %s", e)
        return set()


def _ensure_cache():
    global _uploaded_files
    if _uploaded_files is None:
        _uploaded_files = _load_uploaded_cache()


def is_file_uploaded(filename: str) -> bool:
    """Check if raw data for this file has already been written to InfluxDB."""
    _ensure_cache()
    return filename in _uploaded_files


# ── Writing ────────────────────────────────────────────────────────────────────

def write_raw_data(
    df: pd.DataFrame,
    filename: str,
    base_time: Optional[datetime.datetime] = None,
    event_key: str = "off-field",
    match_info: str = "off-field",
) -> int:
    """
    Write all raw numeric telemetry data from a wpilog DataFrame to InfluxDB.

    Args:
        df: Unpivoted DataFrame (Timestamp index, Key/Value columns).
        filename: The .wpilog filename (used as a tag for filtering).
        base_time: Wall-clock start time extracted from the filename.
            Wpilog microsecond timestamps are added as offsets to this.
        event_key: FRC event code or "off-field".
        match_info: Match identifier (e.g. "Q2") or "off-field".

    Returns:
        Number of points written, or 0 on failure.
    """
    _ensure_cache()

    if is_file_uploaded(filename):
        return 0

    # Compute base epoch in microseconds
    if base_time is not None:
        base_us = int(base_time.timestamp() * 1_000_000)
    else:
        base_us = 0

    # ── Vectorised pre-processing ──────────────────────────────────────────
    df_work = df[["Key", "Value"]].copy()
    df_work["numval"] = pd.to_numeric(df_work["Value"], errors="coerce")
    df_work = df_work.dropna(subset=["numval"])

    if df_work.empty:
        log.warning("InfluxDB: no numeric data in %s", filename)
        return 0

    # Parse keys into (device_type, device_id, signal) using vectorised regex
    parsed = df_work["Key"].str.extract(_KEY_PATTERN)
    parsed.columns = ["device_type", "device_id", "signal"]

    # Non-Phoenix6 keys get generic tags
    mask = parsed["device_type"].isna()
    parsed.loc[mask, "device_type"] = "other"
    parsed.loc[mask, "device_id"] = "0"
    parsed.loc[mask, "signal"] = df_work.loc[mask, "Key"]

    # Merge parsed tags into the working frame
    df_work = df_work.assign(
        device_type=parsed["device_type"].values,
        device_id=parsed["device_id"].values,
        signal=parsed["signal"].values,
    )

    total = len(df_work)
    log.info("  InfluxDB: writing %d points for %s ...", total, filename)

    try:
        with _get_client() as client:
            write_api = client.write_api(write_options=SYNCHRONOUS)

            points = []
            for row in df_work.itertuples():
                # row.Index is the wpilog microsecond timestamp
                abs_ts = base_us + row.Index

                p = (
                    Point("robot_telemetry")
                    .tag("file", filename)
                    .tag("device_type", row.device_type)
                    .tag("device_id", row.device_id)
                    .tag("signal", row.signal)
                    .tag("event_key", event_key)
                    .tag("match_info", match_info)
                    .field("value", row.numval)
                    .time(abs_ts, WritePrecision.US)
                )
                points.append(p)

                if len(points) >= BATCH_SIZE:
                    write_api.write(bucket=INFLUX_BUCKET, record=points)
                    points = []

            # Flush remainder
            if points:
                write_api.write(bucket=INFLUX_BUCKET, record=points)

            # Record that this file is done
            tracking = (
                Point("_upload_tracking")
                .tag("file", filename)
                .field("points", total)
                .field("uploaded", True)
            )
            write_api.write(bucket=INFLUX_BUCKET, record=tracking)

        _uploaded_files.add(filename)
        log.info("  InfluxDB: ✓ %d points written", total)
        return total

    except Exception as e:
        log.error("InfluxDB write failed for %s: %s", filename, e)
        return 0
