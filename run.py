#!/usr/bin/env python3
"""
Robot Telemetry Service — Slap Shot (FRC 4607)

Always-running service that watches input-logs/ for new hoot files,
converts them to wpilog, runs all metric groups, and writes results
to the database.

On startup, processes any pending files, then enters watch mode.
"""

import os
import sys
import glob
import shutil
import subprocess
import hashlib
import mmap
import json
import datetime
import re
import importlib
import importlib.util
import signal
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from watchdog.observers import Observer
from watchdog.events import FileSystemEvent, FileSystemEventHandler

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OWLET_BIN = os.path.join(BASE_DIR, "owlet-26.1.0-linuxx86-64")
INPUT_DIR = os.path.join(BASE_DIR, "input-logs")
LOGS_DIR = os.path.join(BASE_DIR, "archive", "logs")
METRICS_DIR = os.path.join(BASE_DIR, "archive", "metrics")
HOOT_ARCHIVE = os.path.join(BASE_DIR, "archive", "robot-logs")
GROUPS_DIR = os.path.join(BASE_DIR, "groups")

# How long to wait after the last file event before processing a directory (seconds).
SETTLE_SECONDS = 5

from keys import DB_PASSWORD  # noqa: E402 (before sys.path manipulation)
from urllib.parse import quote_plus  # noqa: E402

# ── Database URL (override with RUN_DB_URL env var) ────────────────────────────
_default_db_url = f"postgresql+psycopg2://postgres:{quote_plus(DB_PASSWORD)}@127.0.0.1:5432/stoplight"
DB_URL = os.environ.get("RUN_DB_URL", _default_db_url)

# Patch sys.argv so parse_args / db infrastructure can import cleanly.
sys.argv = [sys.argv[0], "-d", DB_URL, "-g", GROUPS_DIR, "-a", os.path.join(BASE_DIR, "archive")]
sys.path.insert(0, BASE_DIR)

# ── Logging ────────────────────────────────────────────────────────────────────
# Use an explicit handler that flushes after every record so output is visible
# immediately even when stdout/stderr are redirected to a file or pipe.
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

class _FlushHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

_handler = _FlushHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("telemetry")

# ── Imports that depend on sys.argv patching ───────────────────────────────────
from wpilog.datalog import DataLogReader       # noqa: E402
from wpilog.dlutil import WPILogToDataFrame    # noqa: E402
from db.metric import Metric                   # noqa: E402
from db.engine import engine                   # noqa: E402
from db.influxdb_writer import is_file_uploaded, write_raw_data  # noqa: E402
from sqlalchemy.orm import Session             # noqa: E402
from sqlalchemy import Select                  # noqa: E402

# ── Ensure directories exist ──────────────────────────────────────────────────
for _d in [INPUT_DIR, LOGS_DIR, METRICS_DIR, HOOT_ARCHIVE]:
    os.makedirs(_d, exist_ok=True)


# ── Data Classes ───────────────────────────────────────────────────────────────
@dataclass
class GroupInfo:
    name: str = ""
    hash: bytes = bytes()
    module: object = None
    metrics: Dict[str, Tuple[int, str]] = field(default_factory=dict)


# ── Metric Groups ─────────────────────────────────────────────────────────────
def load_groups() -> List[GroupInfo]:
    """Import every .py file in the groups directory as a metric group."""
    groups = []
    for fname in sorted(os.listdir(GROUPS_DIR)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        modname = fname[:-3]
        fpath = os.path.join(GROUPS_DIR, fname)
        spec = importlib.util.spec_from_file_location(modname, fpath)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            log.error("Failed to import group %s: %s", fname, e)
            sys.exit(1)
        with open(fpath, "rb") as f:
            h = hashlib.file_digest(f, "md5").digest()
        groups.append(GroupInfo(name=modname, hash=h, module=mod))
    return groups


# ── Hoot Conversion ───────────────────────────────────────────────────────────
def convert_hoot(hoot_path: str) -> Optional[str]:
    """Convert a single .hoot → .wpilog. Returns output path or None on failure."""
    base = os.path.splitext(os.path.basename(hoot_path))[0]
    out_path = os.path.join(LOGS_DIR, f"{base}.wpilog")
    if os.path.exists(out_path):
        return out_path
    result = subprocess.run(
        [OWLET_BIN, hoot_path, out_path, "-f", "wpilog", "--unlicensed"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("Conversion failed for %s: %s", os.path.basename(hoot_path), result.stderr.strip())
        return None
    return out_path


def process_hoot_directory(hoot_dir: str, groups: List[GroupInfo]) -> int:
    """Convert all hoots in a directory, run metrics, and archive the originals."""
    dirname = os.path.basename(hoot_dir)
    hoot_files = sorted(glob.glob(os.path.join(hoot_dir, "*.hoot")))
    if not hoot_files:
        return 0

    log.info("Processing directory: %s (%d hoot files)", dirname, len(hoot_files))

    wpilog_paths: List[str] = []
    all_ok = True

    for hoot in hoot_files:
        out = convert_hoot(hoot)
        if out:
            log.info("  ✓ Converted %s", os.path.basename(hoot))
            wpilog_paths.append(out)
        else:
            all_ok = False

    # Run metrics on the freshly-converted files
    total_metrics = 0
    for wpilog_path in wpilog_paths:
        total_metrics += analyze_log(wpilog_path, groups)

    # Archive originals if every conversion succeeded
    if all_ok:
        for hoot in hoot_files:
            if not os.path.exists(hoot):
                continue
            dest = os.path.join(HOOT_ARCHIVE, os.path.basename(hoot))
            if not os.path.exists(dest):
                shutil.move(hoot, dest)
            else:
                os.remove(hoot)
        try:
            os.rmdir(hoot_dir)
        except OSError:
            pass  # dir not empty (unexpected extra files) — leave it

    return total_metrics


# ── Log Analysis ───────────────────────────────────────────────────────────────
def get_info_from_log_name(name: str) -> dict:
    """Extract timestamp and event info from the log filename."""
    base = name.rsplit(".", 1)[0]

    date_match = re.search(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", base)
    if date_match:
        try:
            dt = (
                datetime.datetime.strptime(date_match.group(1), "%Y-%m-%d_%H-%M-%S")
                .replace(tzinfo=datetime.timezone.utc)
                .astimezone()
            )
            return {"fileName": name, "dt": dt, "event": None, "matchInfo": None}
        except ValueError:
            pass

    parts = base.split("_")
    if len(parts) >= 3 and parts[1] != "TBD":
        try:
            dt = (
                datetime.datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
                .replace(tzinfo=datetime.timezone.utc)
                .astimezone()
            )
            event = parts[3] if len(parts) > 3 else None
            match = parts[4] if len(parts) > 4 else None
            return {"fileName": name, "dt": dt, "event": event, "matchInfo": match}
        except (ValueError, IndexError):
            pass

    return {"fileName": name, "dt": None, "event": None, "matchInfo": None}


def extract_fms_info(df) -> Tuple[str, str]:
    """Extract FMS match identification from the wpilog DataFrame.

    Returns (event_key, match_info).  If FMS data is absent or the robot
    was not connected to FMS, returns ("off-field", "off-field").
    """
    fms_rows = df[df["Key"].str.startswith("FMS/", na=False)]
    if fms_rows.empty:
        return ("off-field", "off-field")

    event_name = ""
    match_number = 0
    match_type = "None"

    for _, row in fms_rows.iterrows():
        key, val = row["Key"], row["Value"]
        if key == "FMS/EventName" and val:
            event_name = str(val)
        elif key == "FMS/MatchNumber":
            try:
                match_number = int(val)
            except (ValueError, TypeError):
                pass
        elif key == "FMS/MatchType" and val:
            match_type = str(val)

    if match_type == "None" or not event_name:
        return ("off-field", "off-field")

    prefix = {"Practice": "P", "Qualification": "Q", "Elimination": "E"}.get(
        match_type, match_type[0] if match_type else "?"
    )
    return (event_name, f"{prefix}{match_number}")


def analyze_log(path: str, groups: List[GroupInfo]) -> int:
    """Run all metric groups on a single .wpilog file and write results to the DB."""
    filename = os.path.basename(path)

    # Fast-path: check if all groups are already computed for this filename
    # by comparing group hashes.  This avoids parsing large files needlessly.
    current_group_hashes = set()
    for group in groups:
        with open(group.module.__file__, "rb") as f:
            group.hash = hashlib.file_digest(f, "md5").digest()
        current_group_hashes.add(group.hash)

    with Session(engine) as sess:
        existing_hashes = set(
            row[0] for row in sess.execute(
                Select(Metric.metric_hash)
                .where(Metric.file_name == filename)
                .distinct()
            ).all()
        )
    pg_done = current_group_hashes.issubset(existing_hashes)
    influx_done = is_file_uploaded(filename)

    if pg_done and influx_done:
        return 0

    log.info("Analyzing %s ...", filename)

    with open(path, "r") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        file_hash = hashlib.md5(mm).digest()
        reader = DataLogReader(mm)
        df = WPILogToDataFrame(reader)

    info = get_info_from_log_name(filename)

    # Extract FMS match info from the log data itself
    event_key, match_info = extract_fms_info(df)
    info["event"] = event_key
    info["matchInfo"] = match_info

    # Write raw time-series data to InfluxDB (skips automatically if already done)
    if not influx_done:
        write_raw_data(df, filename, base_time=info["dt"],
                       event_key=event_key, match_info=match_info)

    # If all stoplight metrics are already in PostgreSQL, we're done
    if pg_done:
        return 0

    results: Dict[str, GroupInfo] = {}

    for group in groups:
        group.metrics = {}

        # Recompute group hash so hot-reloaded edits are picked up
        with open(group.module.__file__, "rb") as f:
            group.hash = hashlib.file_digest(f, "md5").digest()

        # Skip if already computed for this (file, group-version) pair
        with Session(engine) as sess:
            prev = sess.scalar(
                Select(Metric.id)
                .where(Metric.file_hash == file_hash)
                .where(Metric.metric_hash == group.hash)
            )
        if prev is not None:
            continue

        metric_defs = group.module.defineMetrics()
        for name, fn in metric_defs.items():
            severity, result = fn(df)
            group.metrics[name] = (severity, result)
        results[group.name] = group

    if not results:
        log.info("  All groups already computed for %s", filename)
        return 0

    # Write JSON backup
    try:
        jDict: dict = {"hash": file_hash.hex()}
        for key in results:
            jDict[key] = {
                "hash": results[key].hash.hex(),
                "metrics": results[key].metrics,
            }
        ts = str(datetime.datetime.today()).replace(" ", "_").replace(".", "-").replace(":", "-")
        jFilePath = os.path.join(METRICS_DIR, f"{filename.replace('.', '_')}_{ts}.json")
        with open(jFilePath, "w") as f:
            json.dump(jDict, f)
    except Exception as e:
        log.warning("JSON write failed: %s", e)

    # Write to database
    rows = []
    for gname, ginfo in results.items():
        for mname, (severity, result) in ginfo.metrics.items():
            rows.append(
                Metric(
                    file_hash=file_hash,
                    metric_hash=ginfo.hash,
                    file_name=info["fileName"],
                    group=gname,
                    metric=mname,
                    value=result,
                    stoplight=severity,
                    log_timestamp=info["dt"],
                    event_key=info["event"],
                    match_info=info["matchInfo"],
                )
            )
    with Session(engine) as sess:
        sess.add_all(rows)
        sess.commit()

    log.info("  ✓ %d metrics written to DB", len(rows))
    return len(rows)


# ── File System Watcher ───────────────────────────────────────────────────────
class HootDirectoryHandler(FileSystemEventHandler):
    """Watches input-logs/ and processes new hoot directories after they settle.

    "Settle" means no new filesystem events for SETTLE_SECONDS — this ensures
    all files in a directory have finished being copied before we start
    processing.
    """

    def __init__(self, groups: List[GroupInfo]):
        super().__init__()
        self.groups = groups
        self._timers: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _schedule_processing(self, dir_path: str):
        """(Re-)schedule processing of a directory after the settle period."""
        with self._lock:
            if dir_path in self._timers:
                self._timers[dir_path].cancel()
            timer = threading.Timer(SETTLE_SECONDS, self._process_directory, args=[dir_path])
            timer.daemon = True
            timer.start()
            self._timers[dir_path] = timer

    def _process_directory(self, dir_path: str):
        with self._lock:
            self._timers.pop(dir_path, None)

        if not os.path.isdir(dir_path):
            return
        if not glob.glob(os.path.join(dir_path, "*.hoot")):
            return

        try:
            n = process_hoot_directory(dir_path, self.groups)
            if n:
                log.info("Directory %s complete: %d metrics written", os.path.basename(dir_path), n)
        except Exception:
            log.error("Error processing %s", dir_path, exc_info=True)

    def on_any_event(self, event: FileSystemEvent):
        src = event.src_path
        rel = os.path.relpath(src, INPUT_DIR)
        top_dir = rel.split(os.sep)[0]
        if top_dir == ".":
            return
        target = os.path.join(INPUT_DIR, top_dir)
        if os.path.isdir(target):
            self._schedule_processing(target)


# ── Initial Scan ──────────────────────────────────────────────────────────────
def initial_scan(groups: List[GroupInfo]):
    """Process any pending hoot directories and unanalyzed wpilog files."""
    # Convert pending hoots
    if os.path.isdir(INPUT_DIR):
        pending = sorted(
            d for d in os.listdir(INPUT_DIR)
            if os.path.isdir(os.path.join(INPUT_DIR, d))
            and glob.glob(os.path.join(INPUT_DIR, d, "*.hoot"))
        )
        if pending:
            log.info("Initial scan: %d hoot directories to process", len(pending))
            for dirname in pending:
                process_hoot_directory(os.path.join(INPUT_DIR, dirname), groups)

    # Analyze any unprocessed wpilog files
    logs = sorted(f for f in os.listdir(LOGS_DIR) if f.endswith(".wpilog"))
    if logs:
        log.info("Initial scan: checking %d wpilog files for unrun metrics", len(logs))
        total = 0
        for logfile in logs:
            total += analyze_log(os.path.join(LOGS_DIR, logfile), groups)
        if total:
            log.info("Initial scan complete: %d metrics written", total)
        else:
            log.info("Initial scan complete: all metrics up to date")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("Robot Telemetry Service — Slap Shot (FRC 4607)")
    log.info("=" * 60)

    if not os.path.isfile(OWLET_BIN):
        log.error("owlet binary not found at %s", OWLET_BIN)
        sys.exit(1)

    # Load metric groups
    groups = load_groups()
    log.info("Loaded %d groups: %s", len(groups), ", ".join(g.name for g in groups))

    # Start watching for new files BEFORE the initial scan so nothing
    # is missed if files arrive while the scan is running.
    handler = HootDirectoryHandler(groups)
    observer = Observer()
    observer.schedule(handler, INPUT_DIR, recursive=True)
    observer.start()
    log.info("Watching %s for new hoot files ...  (send SIGTERM or Ctrl+C to stop)", INPUT_DIR)

    # Process anything already pending
    initial_scan(groups)

    # Graceful shutdown
    shutdown = threading.Event()

    def on_signal(signum, _frame):
        log.info("Received signal %d, shutting down ...", signum)
        shutdown.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    try:
        shutdown.wait()
    finally:
        observer.stop()
        observer.join()
        log.info("Service stopped.")


if __name__ == "__main__":
    main()
