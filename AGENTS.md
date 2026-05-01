# AGENTS.md - Robot Telemetry AI Contributor Playbook

Last updated: 2026-04-30
Repository: FRC4607/Robot-Telemetry
Primary development branch: 2026-wip

This file is the execution playbook for coding agents working in this repo.
It is intentionally prescriptive.

## 1) Mission

Keep the telemetry pipeline correct, fast, and operationally predictable.

System flow:
1. Receive Phoenix6 .hoot files in input-logs/
2. Upload path A: pit-logfiles-retriever-app pulls from RoboRIO and forwards to upload API
3. Upload path B: upload_server.py accepts .hoot uploads to input-logs/
4. Convert .hoot to .wpilog using owlet
5. Evaluate metric groups in groups/
6. Write stoplight metrics to PostgreSQL metrics table
7. Stream raw numeric signals to InfluxDB robot_telemetry
8. Power Grafana dashboards

## 2) Non-Negotiable Rules

1. Make minimal, localized changes.
2. Do not rename metric names unless explicitly requested.
3. Keep threshold logic config-driven via config/metric_thresholds.json.
4. Validate behavior after edits with concrete checks, not only static reads.
5. For destructive actions, enumerate targets before deletion.
6. Keep AGENTS.md current whenever architecture, workflow, ownership, or operational commands change.
7. After any logic change, perform an AGENTS.md consistency pass before considering work complete.

## 3) Current Behavior That Must Be Preserved

### 3.1 rio filtering and auto-deletion

rio-only logs are handled in three stages in run.py:
1. Auto-deletion in initial_scan (startup): rio .hoot files are deleted from input-logs/ before any processing
2. Auto-deletion in _process_hoot_directory (watch mode): rio .hoot files are deleted when detected during runtime uploads
3. Analysis-time safety skip in analyze_log: additional protection layer that skips any rio signals found in processed files

Expected result: files containing _rio_ should not accumulate on disk, should not produce new metrics, and should
not be converted from .hoot when seen in hoot directories. Detection covers both patterns: names containing _rio_ and names starting with rio_. All rio files are deleted with explicit logging.

### 3.2 Reprocessing model

Startup scans both:
1. input-logs/ for .hoot directories
2. archive/logs/ for .wpilog files

If DB rows are removed but files remain on disk, files can be processed again.
DB cleanup and filesystem cleanup are separate concerns.

### 3.3 Group parallelism

Metric groups run concurrently in analyze_log using ThreadPoolExecutor.
Worker cap:
MAX_METRIC_WORKERS = min(os.cpu_count() or 4, 12)

Single-group workloads use a direct execution fast path.

### 3.4 Upload rejection preservation

If upload_server.py rejects a .hoot during owlet content validation, the file is preserved:
1. Moved to archive/robot-logs/
2. Renamed with suffix _rejected-processing before .hoot
3. Input upload directory is cleaned up when left empty

Expected result: un-processable uploads are retained for debugging instead of being lost.

## 4) Required Preflight Before Any Code Edit

1. Read run.py and at least one impacted group module.
2. Read config/metric_thresholds.py and config/metric_thresholds.json if any stoplight logic is touched.
3. Identify whether change affects PostgreSQL, InfluxDB, or both.
4. Confirm whether dashboard regeneration is required.

Do not start editing until these checks are complete.

## 5) Required Post-Edit Validation

Choose the smallest relevant validation set, but always run at least one runtime check.

### 5.1 Runtime/service checks

1. Tail service logs and verify expected behavior appears in logs.
2. Confirm no new traceback is emitted during the changed code path.

### 5.2 Data checks

If metrics path changed:
1. Verify new rows are written to PostgreSQL metrics.
2. Verify stoplight severities and messages match intended thresholds.

If Influx path changed:
1. Verify robot_telemetry points are written.
2. Verify _upload_tracking dedupe records are correct.

If dashboard path changed:
1. Regenerate dashboards.
2. Verify variable behavior and table fields (including Level labels).

### 5.3 AGENTS.md consistency check (mandatory)

After any logic/behavioral change, verify AGENTS.md still matches reality.

Minimum checks:
1. Confirm impacted files/features are represented in Sections 1, 3, and 7.
2. Confirm operator commands in Section 11 still work for changed workflows.
3. Update AGENTS.md in the same patch when behavior or ownership changed.
4. In the summary/PR notes, explicitly state whether AGENTS.md was reviewed and updated.

## 6) PR Checklist Template (Required)

For every PR or patch summary, include these items:

1. Scope
- What changed and what did not.

2. Risk assessment
- Potential regressions and why risk is low/medium/high.

3. Validation evidence
- Commands run.
- Key outputs observed.

4. Data impact
- Whether historical data, dedupe state, or archive files are affected.

5. Operator actions
- Any required systemctl restart, dashboard regeneration, or cleanup steps.

6. AGENTS.md sync
- Confirm AGENTS.md was reviewed after logic changes.
- List exact AGENTS.md updates made, or state why none were required.

## 7) Canonical Files and Ownership

- run.py: conversion, scan, concurrency, orchestration
- upload_server.py: HTTP upload endpoint and upload validation for incoming .hoot logs
- pit-logfiles-retriever-app/app.py: pit-side retriever that pulls logs from RoboRIO and uploads to cloud API
- setup-ubuntu.sh: one-shot Ubuntu provisioning for PostgreSQL, InfluxDB, Grafana, services, dashboards
- groups/*.py: metric definitions and returned severity/result strings
- config/device_map.py: CAN ID mapping helpers
- config/metric_thresholds.json: canonical threshold and message values
- config/metric_thresholds.py: threshold load helpers and severity comparison helpers
- metric_cache.py: thread-safe DataFrame-derived cache access
- db/metric.py: PostgreSQL schema model for metrics table
- db/influxdb_writer.py: raw stream writer and file dedupe tracking
- generate_dashboards.py: dashboard build/upload behavior
- reset-databases.sh: destructive DB cleanup

## 8) Metric and Message Conventions

1. Prefer get_threshold(path, default) over hardcoded constants.
2. Prefer high_is_bad / low_is_bad helper usage for consistency.
3. Keep result messages explicit and operator-readable.
4. Current semantic convention:
- avg_current and max_current usually refer to stator current.
- power.max_total_current refers to summed supply current.
- faults.log_duration_s flags short captured logs that may indicate restart/reboot/partial recording.

## 9) InfluxDB Dedupe Requirements

influxdb_writer uses:
1. robot_telemetry measurement for raw points
2. _upload_tracking measurement for uploaded-file state

If deleting raw file data in Influx, delete matching _upload_tracking entries for the same file tags, or is_file_uploaded can produce stale skips.

## 10) Dashboard Guardrails

generate_dashboards.py currently assumes:
1. Match Stoplight excludes rio files in variable queries using both patterns: _rio_ and rio_ prefix
2. Event, Match, and Log Session default to an internal latest sentinel while displaying the real latest value text and still listing all historical options for manual selection; the latest event resolver ignores off-field, and Log Session aggregates segmented sibling files (.wpilog, .2.wpilog, .3.wpilog)
3. Match Stoplight includes a Log Duration stat computed from InfluxDB _upload_tracking min_time_us/max_time_us across segmented sibling files
4. Table uses Level text label for severity
5. Periodic refresh is enabled for live operation

When changing metric names, group names, schema fields, or datasource UIDs, regenerate and re-upload dashboards.

## 11) Standard Operator Commands

Pit retriever app:

```bash
cd pit-logfiles-retriever-app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Upload server:

```bash
source bin/activate
python3 upload_server.py --host 0.0.0.0 --port 8080
```

Manual run:

```bash
source bin/activate
python3 run.py
```

Fresh VM setup script:

```bash
sudo ./setup-ubuntu.sh
```

Service operations:

```bash
systemctl status robot-telemetry
journalctl -u robot-telemetry -f
systemctl restart robot-telemetry
```

Destructive reset:

```bash
sudo ./reset-databases.sh
```

After a reset, remove stale old-year and _rio_ files from input-logs/ and archive/ if you do not want them reprocessed.

## 12) Common Failure Modes

1. parse_args side effects from import order around run.py setup.
2. Reprocessing surprises when DB is cleaned but files remain on disk.
3. Thread safety bugs from mutating DataFrame attrs caches.
4. owlet conversion failures on old hoot versions.
5. Hood signal prefix mismatch (TalonFX vs TalonFXS).

## 13) Quick Health Check Sequence

1. Confirm service health in logs.
2. Confirm no stale _rio_ or old-year files in watched/archive trees.
3. Confirm PostgreSQL metrics writes for recent files.
4. Confirm Influx raw writes and upload tracking writes.
5. Confirm Match Stoplight resolves to latest valid non-rio file.

## 14) Coverage Snapshot

Active 2026 groups:
- swerve
- intake_arm
- intake_wheels
- indexer
- chamber
- turret
- flywheel
- hood
- imu
- power
- faults

Historical 2023 group code has been removed from this repository.

## 15) Reference Context

For broad project history and robot context, see AGENT_ONBOARDING.md.
For day-to-day coding behavior and acceptance criteria, use this file.