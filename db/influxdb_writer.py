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
import struct
import logging
import datetime
import mmap
import concurrent.futures
from typing import Optional, Set

import pandas as pd
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from keys import INFLUX_TOKEN as _DEFAULT_TOKEN
from wpilog.datalog import DataLogReader, StartRecordData, WPILogEntryToType

log = logging.getLogger("telemetry")

# ── Connection settings (overridable via environment variables) ─────────────
INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", _DEFAULT_TOKEN)
INFLUX_ORG = os.environ.get("INFLUX_ORG", "frc4607")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "robot-telemetry")

BATCH_SIZE = 50_000
WRITE_WORKERS = 4

# Parse Phoenix6 signal keys: "Phoenix6/TalonFX-1/StatorCurrent"
_KEY_PATTERN = re.compile(r"^Phoenix6/(\w+)-(\d+)/(.+)$")

# Module-level cache of already-uploaded filenames (lazy-initialized).
_uploaded_files: Optional[Set[str]] = None


def _get_client() -> InfluxDBClient:
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG,
                          enable_gzip=True)


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

def _escape_tag(s: str) -> str:
    """Escape special characters in InfluxDB line protocol tag values."""
    return s.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _parse_key(key: str):
    """Parse a wpilog key into (device_type, device_id, signal)."""
    m = _KEY_PATTERN.match(key)
    if m:
        return m.group(1), m.group(2), m.group(3)
    return "other", "0", key


def stream_raw_data(
    wpilog_path: str,
    filename: str,
    base_time: Optional[datetime.datetime] = None,
    event_key: str = "off-field",
    match_info: str = "off-field",
) -> int:
    """
    Stream raw numeric telemetry data directly from a wpilog file to InfluxDB.

    Instead of building a full DataFrame in RAM, this reads records one at a time
    from the wpilog, converts numeric values to line-protocol strings, and writes
    them in batches.  Peak memory usage is O(BATCH_SIZE) instead of O(file_size).

    Uses gzip compression, large batches, cached tag prefixes, and a thread pool
    for overlapped writes so parsing never blocks on InfluxDB I/O.

    Returns number of points written, or 0 on failure.
    """
    _ensure_cache()
    if is_file_uploaded(filename):
        return 0

    base_us = int(base_time.timestamp() * 1_000_000) if base_time else 0

    # Pre-escape constant tag values once
    e_filename = _escape_tag(filename)
    e_event = _escape_tag(event_key)
    e_match = _escape_tag(match_info)

    # Cache: entry_id → pre-built tag prefix string (everything before " value=")
    tag_cache: dict[int, str] = {}

    total = 0
    lines: list[str] = []

    try:
        with open(wpilog_path, "r") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

        reader = DataLogReader(mm)
        start_records: dict[int, StartRecordData] = {}

        with _get_client() as client:
            write_api = client.write_api(write_options=SYNCHRONOUS)

            # Thread pool for overlapped batch writes
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=WRITE_WORKERS)
            futures: list[concurrent.futures.Future] = []

            def _flush(batch: str):
                write_api.write(bucket=INFLUX_BUCKET, record=batch)

            for record in reader:
                if record.isStart():
                    sd = record.getStartData()
                    start_records[sd.entry] = sd
                    continue
                if record.isControl():
                    continue
                if record.entry not in start_records:
                    continue

                sr = start_records[record.entry]
                # Only write numeric types
                if sr.type not in ("double", "int64"):
                    continue

                try:
                    if sr.type == "double":
                        val = record.getDouble()
                    else:
                        val = float(record.getInteger())
                except (TypeError, struct.error):
                    continue

                # Cached tag prefix per entry ID
                prefix = tag_cache.get(record.entry)
                if prefix is None:
                    device_type, device_id, signal_name = _parse_key(sr.name)
                    prefix = (
                        f"robot_telemetry,"
                        f"file={e_filename},"
                        f"device_type={_escape_tag(device_type)},"
                        f"device_id={_escape_tag(device_id)},"
                        f"signal={_escape_tag(signal_name)},"
                        f"event_key={e_event},"
                        f"match_info={e_match}"
                    )
                    tag_cache[record.entry] = prefix

                abs_ts = base_us + record.timestamp
                lines.append(f"{prefix} value={val} {abs_ts}000")
                total += 1

                if len(lines) >= BATCH_SIZE:
                    batch = "\n".join(lines)
                    futures.append(pool.submit(_flush, batch))
                    lines = []

                    if total % 1_000_000 == 0:
                        log.info("    InfluxDB: %dM points streamed ...", total // 1_000_000)

                    # Don't let too many futures pile up (back-pressure)
                    if len(futures) >= WRITE_WORKERS * 2:
                        done, _ = concurrent.futures.wait(
                            futures, return_when=concurrent.futures.FIRST_COMPLETED
                        )
                        for fut in done:
                            fut.result()  # raise on error
                            futures.remove(fut)

            # Flush remainder
            if lines:
                futures.append(pool.submit(_flush, "\n".join(lines)))

            # Wait for all writes to finish
            for fut in concurrent.futures.as_completed(futures):
                fut.result()

            pool.shutdown(wait=False)

            # Record that this file is done
            tracking = (
                Point("_upload_tracking")
                .tag("file", filename)
                .field("points", total)
                .field("uploaded", True)
            )
            write_api.write(bucket=INFLUX_BUCKET, record=tracking)

        mm.close()
        _uploaded_files.add(filename)
        log.info("  InfluxDB: ✓ %d points streamed", total)
        return total

    except Exception as e:
        log.error("InfluxDB stream write failed for %s: %s", filename, e)
        return 0


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
