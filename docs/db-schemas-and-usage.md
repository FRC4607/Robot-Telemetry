# Database Schema Reference

This project uses **two databases** to store robot telemetry data:

| Database   | Purpose | Access |
|------------|---------|--------|
| **PostgreSQL** | Stoplight metric results (pass/warn/fail summaries per log file) | SQLAlchemy / SQL |
| **InfluxDB 2.x** | Raw time-series sensor data from every log file | Flux queries / Grafana |

Both are populated automatically by `run.py` when new `.hoot` log files appear in `input-logs/`.

---

## PostgreSQL — Stoplight Metrics

### Connection

| Parameter | Default |
|-----------|---------|
| Host | `127.0.0.1:5432` |
| Database | `stoplight` |
| User | `postgres` |
| Password | Set via `DB_PASSWORD` in `.env` (loaded by `keys.py`) |
| SQLAlchemy URL format | `postgresql+psycopg2://postgres:<password>@127.0.0.1:5432/stoplight` |

You can override the connection URL by setting the `RUN_DB_URL` environment variable.

Migrations are managed with **Alembic** (see `alembic/` directory and `alembic.ini`).

### Table: `metrics`

Every row represents a single computed metric for one log file. Metrics are grouped into subsystem "groups" (Python files in `groups/`), and each group's `defineMetrics()` function produces one or more named metrics.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INTEGER` (PK, auto-increment) | No | Unique row identifier. |
| `file_hash` | `BYTEA(16)` | No | MD5 hash of the `.wpilog` file contents. Used for deduplication. |
| `metric_hash` | `BYTEA(16)` | No | MD5 hash of the group `.py` file that produced this metric. If a group file changes, metrics are re-run for existing logs. |
| `file_name` | `VARCHAR(1024)` | No | Original `.wpilog` filename (e.g. `FC3906C24C32435320202059253C17FF_2026-04-07_00-21-04.wpilog`). |
| `group` | `VARCHAR(1024)` | No | Name of the metric group (Python module name without `.py`, e.g. `chamber`, `swerve`, `power`). |
| `metric` | `VARCHAR(1024)` | No | Human-readable metric name (e.g. `Left Chamber Max Current`, `FL Drive Max Velocity`). |
| `value` | `VARCHAR(1024)` | No | The metric result as a string (e.g. `42.3 A`, `1.8 m/s`, `metric_not_implemented`). |
| `stoplight` | `SMALLINT` | No | Severity code: **0** = green (OK), **1** = yellow (warning), **2** = red (critical), **-1** = not implemented / insufficient data. |
| `metric_timestamp` | `TIMESTAMP` | No | When the metric was written to the database (auto-set by `now()`). |
| `log_timestamp` | `TIMESTAMP` | Yes | Wall-clock time the log was recorded, extracted from the filename. |
| `event_key` | `VARCHAR(256)` | Yes | FRC event code (e.g. `2026mndu`) or `off-field` if not connected to FMS. |
| `match_info` | `VARCHAR(16)` | Yes | Match identifier (e.g. `Q2`, `E5`, `P1`) or `off-field`. |

### Useful Queries

```sql
-- All red-stoplight metrics for a specific event
SELECT file_name, "group", metric, value, log_timestamp, match_info
FROM metrics
WHERE stoplight = 2 AND event_key = '2026mndu'
ORDER BY log_timestamp;

-- Latest metrics for the most recent log file
SELECT "group", metric, value, stoplight
FROM metrics
WHERE file_name = (SELECT file_name FROM metrics ORDER BY metric_timestamp DESC LIMIT 1)
ORDER BY "group", metric;

-- Trend of a specific metric across all logs
SELECT file_name, value, stoplight, log_timestamp, match_info
FROM metrics
WHERE metric = 'Left Chamber Max Current'
ORDER BY log_timestamp;

-- Count metrics by stoplight status per group
SELECT "group", stoplight, COUNT(*)
FROM metrics
GROUP BY "group", stoplight
ORDER BY "group", stoplight;
```

### Deduplication

Metrics are keyed on `(file_hash, metric_hash)`. If you modify a group file (e.g. `groups/chamber.py`), its MD5 hash changes, so `run.py` will automatically re-compute metrics for all log files on the next run (or if started with `--rescan`).

---

## InfluxDB 2.x — Raw Time-Series Data

### Connection

| Parameter | Default | Env Override |
|-----------|---------|--------------|
| URL | `http://localhost:8086` | `INFLUX_URL` |
| Organization | `frc4607` | `INFLUX_ORG` |
| Bucket | `robot-telemetry` | `INFLUX_BUCKET` |
| Token | Set via `INFLUX_TOKEN` in `.env` | `INFLUX_TOKEN` |

### Measurement: `robot_telemetry`

Every numeric data point from the robot's CAN bus log is written here. This is the primary measurement used for Grafana dashboards and detailed signal analysis.

| Component | Name | Description |
|-----------|------|-------------|
| **Measurement** | `robot_telemetry` | Fixed measurement name for all sensor data. |
| **Tag** | `file` | The `.wpilog` filename this data came from. |
| **Tag** | `device_type` | Hardware type: `TalonFX`, `CANcoder`, `Pigeon2`, or `other`. |
| **Tag** | `device_id` | CAN bus ID as a string (e.g. `6`, `23`). |
| **Tag** | `signal` | Signal name within the device (e.g. `StatorCurrent`, `Velocity`, `Position`). |
| **Tag** | `event_key` | FRC event code or `off-field`. |
| **Tag** | `match_info` | Match identifier (e.g. `Q2`) or `off-field`. |
| **Field** | `value` | The numeric signal value (`float64`). |
| **Timestamp** | | Wall-clock time in **microsecond precision**. Computed as `log_start_time + wpilog_offset`. |

#### Signal Key Parsing

Raw wpilog keys follow the pattern `Phoenix6/<DeviceType>-<ID>/<Signal>`. For example:

- `Phoenix6/TalonFX-6/StatorCurrent` → `device_type=TalonFX`, `device_id=6`, `signal=StatorCurrent`
- `Phoenix6/CANcoder-15/Position` → `device_type=CANcoder`, `device_id=15`, `signal=Position`
- `Phoenix6/Pigeon2-0/Yaw` → `device_type=Pigeon2`, `device_id=0`, `signal=Yaw`

Keys that don't match this pattern get `device_type=other`, `device_id=0`, and the full key as `signal`.

#### Common Signals

| Signal | Devices | Unit | Description |
|--------|---------|------|-------------|
| `StatorCurrent` | TalonFX | Amps | Current through the motor windings. |
| `SupplyCurrent` | TalonFX | Amps | Current drawn from the battery. |
| `SupplyVoltage` | TalonFX | Volts | Battery voltage seen by the motor controller. |
| `MotorVoltage` | TalonFX | Volts | Voltage applied to the motor. |
| `Velocity` | TalonFX, CANcoder | rot/s or rot/s | Rotational velocity. |
| `Position` | TalonFX, CANcoder | rotations | Accumulated position. |
| `Yaw` | Pigeon2 | degrees | Robot heading. |
| `Pitch` | Pigeon2 | degrees | Forward/back tilt. |
| `Roll` | Pigeon2 | degrees | Side-to-side tilt. |
| `AngularVelocityZWorld` | Pigeon2 | deg/s | Yaw rate. |

#### CAN ID → Subsystem Map

| CAN ID(s) | Device Type | Subsystem |
|------------|-------------|-----------|
| 23, 0, 2, 21 | TalonFX | Swerve drive motors (FL, FR, BL, BR) |
| 22, 1, 3, 20 | TalonFX | Swerve steer motors (FL, FR, BL, BR) |
| 22, 1, 3, 20 | CANcoder | Swerve encoders (FL, FR, BL, BR) |
| 15 | TalonFX | Intake arm motor |
| 15 | CANcoder | Intake arm encoder |
| 14 | TalonFX | Intake wheels motor |
| 13 | TalonFX | Indexer motor |
| 6 | TalonFX | Left chamber motor |
| 17 | TalonFX | Right chamber motor |
| 7 | TalonFX | Left turret motor |
| 31, 32 | CANcoder | Left turret encoders |
| 16 | TalonFX | Right turret motor |
| 41, 42 | CANcoder | Right turret encoders |
| 8 | TalonFX | Left hood motor |
| 9 | TalonFX | Right hood motor |
| 4, 5 | TalonFX | Left flywheel motors |
| 19, 18 | TalonFX | Right flywheel motors |
| 0 | Pigeon2 | IMU |

### Measurement: `_upload_tracking`

Internal bookkeeping measurement used to avoid re-uploading the same log file.

| Component | Name | Description |
|-----------|------|-------------|
| **Tag** | `file` | The `.wpilog` filename. |
| **Field** | `points` | Number of data points written for this file (`int`). |
| **Field** | `uploaded` | Always `true` — presence of a record means the file is done. |

### Useful Flux Queries

```flux
// All signals from a specific log file
from(bucket: "robot-telemetry")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "robot_telemetry")
  |> filter(fn: (r) => r.file == "FC3906C24C32435320202059253C17FF_2026-04-07_00-21-04.wpilog")

// Stator current for left chamber motor (TalonFX-6) in a time range
from(bucket: "robot-telemetry")
  |> range(start: 2026-04-07T00:00:00Z, stop: 2026-04-08T00:00:00Z)
  |> filter(fn: (r) => r._measurement == "robot_telemetry")
  |> filter(fn: (r) => r.device_type == "TalonFX" and r.device_id == "6")
  |> filter(fn: (r) => r.signal == "StatorCurrent")

// Average current per motor during a specific match
from(bucket: "robot-telemetry")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "robot_telemetry")
  |> filter(fn: (r) => r.event_key == "2026mndu" and r.match_info == "Q2")
  |> filter(fn: (r) => r.signal == "StatorCurrent")
  |> group(columns: ["device_type", "device_id"])
  |> mean()

// List all uploaded files
from(bucket: "robot-telemetry")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "_upload_tracking")
  |> filter(fn: (r) => r._field == "uploaded")
  |> keep(columns: ["file"])
  |> distinct(column: "file")
```

---

## Grafana Dashboards

Pre-built dashboards are in `dashboards/` and can be regenerated with `python generate_dashboards.py`.

| Dashboard | Data Source | Description |
|-----------|-------------|-------------|
| **Match Stoplight** | PostgreSQL | Grid of pass/warn/fail metrics per log file. Filter by event and match. |
| **Signal Explorer** | InfluxDB | Interactive dropdown to pick any device + signal and plot it over time. |
| **Subsystem Overview** | InfluxDB | Pre-built panels for each subsystem (current, velocity, position). |
| **Quick View** | InfluxDB | At-a-glance view of the most recent log data. |
| **Select by Creation Time** | InfluxDB | Browse logs by when they were recorded on the robot. |
| **Select by Upload Time** | InfluxDB | Browse logs by when they were uploaded to the server. |

---

## Data Flow Summary

```
Robot (.hoot files)
    │
    ▼
input-logs/          ← files land here (USB or network)
    │
    ▼
run.py               ← converts .hoot → .wpilog, then:
    ├──► PostgreSQL   ← stoplight metrics (one row per metric per file)
    ├──► InfluxDB     ← raw numeric time-series (every data point)
    └──► archive/     ← .wpilog + JSON metric backups
```
