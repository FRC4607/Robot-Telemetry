#!/usr/bin/env python3
"""
Auto-generate Grafana dashboard JSON files from config/device_map.py.

Produces three dashboards:
  1. Match Stoplight — PostgreSQL stoplight metrics, filterable by event/match
  2. Signal Explorer — InfluxDB raw signals with dropdown selectors
  3. Subsystem Overview — InfluxDB pre-built panels per subsystem

Usage:
    python generate_dashboards.py          # write JSON to dashboards/
    python generate_dashboards.py --upload  # also push to Grafana API
"""

import json
import os
import sys
import argparse

# ── Datasource UIDs (from Grafana) ──────────────────────────────────────────
PG_DS = {"type": "grafana-postgresql-datasource", "uid": "ffd8jczdyabr4c"}
INFLUX_DS = {"type": "influxdb", "uid": "fffnbhv0kdukgc"}

INFLUX_BUCKET = "robot-telemetry"
DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards")

# ── Subsystem → (device_type, device_ids, label, signals) ──────────────────
# Built from config/device_map.py knowledge
SWERVE_MODULES = {
    "Front Left":  {"drive": 23, "steer": 22, "cancoder": 22},
    "Front Right": {"drive": 0,  "steer": 1,  "cancoder": 1},
    "Back Left":   {"drive": 2,  "steer": 3,  "cancoder": 3},
    "Back Right":  {"drive": 21, "steer": 20, "cancoder": 20},
}

SUBSYSTEMS = {
    "Intake Arm":      {"type": "TalonFX", "ids": [15], "cancoder": [15]},
    "Intake Wheels":   {"type": "TalonFX", "ids": [14]},
    "Indexer":         {"type": "TalonFX", "ids": [13]},
    "Left Chamber":    {"type": "TalonFX", "ids": [6]},
    "Right Chamber":   {"type": "TalonFX", "ids": [17]},
    "Left Turret":     {"type": "TalonFX", "ids": [7],  "cancoder": [31, 32]},
    "Right Turret":    {"type": "TalonFX", "ids": [16], "cancoder": [41, 42]},
    "Left Hood":       {"type": "TalonFX", "ids": [8]},
    "Right Hood":      {"type": "TalonFX", "ids": [9]},
    "Left Flywheel":   {"type": "TalonFX", "ids": [4, 5]},
    "Right Flywheel":  {"type": "TalonFX", "ids": [19, 18]},
}

MOTOR_SIGNALS = ["StatorCurrent", "SupplyCurrent", "SupplyVoltage", "MotorVoltage", "Velocity", "Position"]
CANCODER_SIGNALS = ["Position", "Velocity"]
PIGEON_SIGNALS = ["AngularVelocityZWorld", "Yaw", "Pitch", "Roll"]

# ── CAN ID → friendly name (from config/device_map.py) ────────────────────
# Keyed by "DeviceType-ID" since CAN IDs can overlap across types
DEVICE_NAMES = {}
for _mod_name, _ids in SWERVE_MODULES.items():
    _abbr = "".join(w[0] for w in _mod_name.split())  # FL, FR, BL, BR
    DEVICE_NAMES[f"TalonFX-{_ids['drive']}"] = f"{_abbr} Drive"
    DEVICE_NAMES[f"TalonFX-{_ids['steer']}"] = f"{_abbr} Steer"
    DEVICE_NAMES[f"CANcoder-{_ids['cancoder']}"] = f"{_abbr} Encoder"
for _name, _info in SUBSYSTEMS.items():
    for i, _id in enumerate(_info["ids"]):
        suffix = f" {i+1}" if len(_info["ids"]) > 1 else ""
        DEVICE_NAMES[f"{_info['type']}-{_id}"] = f"{_name}{suffix}"
    for i, _id in enumerate(_info.get("cancoder", [])):
        suffix = f" {i+1}" if len(_info.get("cancoder", [])) > 1 else ""
        DEVICE_NAMES[f"CANcoder-{_id}"] = f"{_name} Enc{suffix}"
DEVICE_NAMES["Pigeon2-0"] = "Pigeon2 IMU"


def _device_name(device_type, device_id):
    """Look up friendly name for a device, falling back to type-id."""
    return DEVICE_NAMES.get(f"{device_type}-{device_id}", f"{device_type}-{device_id}")

# ── Grafana panel ID counter ───────────────────────────────────────────────
_panel_id = 0


def next_id():
    global _panel_id
    _panel_id += 1
    return _panel_id


def reset_ids():
    global _panel_id
    _panel_id = 0


# ── Flux query helpers ─────────────────────────────────────────────────────

def flux_timeseries(device_type, device_id, signal, file_var="${file}"):
    """Flux query for a single signal filtered by file variable."""
    return f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: time(v: "${{file_start}}"), stop: time(v: "${{file_end}}"))
  |> filter(fn: (r) => r._measurement == "robot_telemetry")
  |> filter(fn: (r) => r.device_type == "{device_type}")
  |> filter(fn: (r) => r.device_id == "{device_id}")
  |> filter(fn: (r) => r.signal == "{signal}")
  |> filter(fn: (r) => r.file == "{file_var}")
  |> filter(fn: (r) => r._field == "value")
  |> aggregateWindow(every: 200ms, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])'''


def flux_multi_device(device_type, device_ids, signal, file_var="${file}"):
    """Flux query for one signal across multiple device IDs (overlaid)."""
    id_filter = " or ".join(f'r.device_id == "{did}"' for did in device_ids)
    # Build friendly name mapping for the legend
    name_cases = " else ".join(
        f'if r.device_id == "{did}" then "{_device_name(device_type, did)}"'
        for did in device_ids
    )
    name_expr = f"{name_cases} else r.device_id"
    return f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: time(v: "${{file_start}}"), stop: time(v: "${{file_end}}"))
  |> filter(fn: (r) => r._measurement == "robot_telemetry")
  |> filter(fn: (r) => r.device_type == "{device_type}")
  |> filter(fn: (r) => {id_filter})
  |> filter(fn: (r) => r.signal == "{signal}")
  |> filter(fn: (r) => r.file == "{file_var}")
  |> filter(fn: (r) => r._field == "value")
  |> aggregateWindow(every: 200ms, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({{r with _field: {name_expr}}}))
  |> keep(columns: ["_time", "_value", "_field"])'''


# ── Panel builders ─────────────────────────────────────────────────────────

def make_timeseries_panel(title, flux_query, grid_pos, unit="", legend_field="device_id"):
    """Create a Grafana timeseries panel with a Flux query."""
    return {
        "id": next_id(),
        "type": "timeseries",
        "title": title,
        "datasource": INFLUX_DS,
        "gridPos": grid_pos,
        "targets": [{
            "datasource": INFLUX_DS,
            "query": flux_query,
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {
                    "lineWidth": 1,
                    "fillOpacity": 10,
                    "spanNulls": True,
                    "axisBorderShow": False,
                },
            },
            "overrides": [],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "desc"},
            "legend": {"displayMode": "list", "placement": "bottom"},
        },
    }


def make_row_panel(title, y, collapsed=True, panels=None):
    """Create a collapsible row panel."""
    row = {
        "id": next_id(),
        "type": "row",
        "title": title,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "collapsed": collapsed,
    }
    if collapsed and panels:
        row["panels"] = panels
    return row


def make_stat_panel(title, query, grid_pos, datasource=None, color=None, thresholds=None):
    """Create a stat panel (single value display)."""
    ds = datasource or PG_DS
    field_config = {"defaults": {}, "overrides": []}
    if color:
        field_config["defaults"]["color"] = {"mode": "fixed", "fixedColor": color}
    if thresholds:
        field_config["defaults"]["color"] = {"mode": "thresholds"}
        field_config["defaults"]["thresholds"] = thresholds
    return {
        "id": next_id(),
        "type": "stat",
        "title": title,
        "datasource": ds,
        "gridPos": grid_pos,
        "targets": [{
            "datasource": ds,
            "rawSql": query,
            "format": "table",
            "rawQuery": True,
            "refId": "A",
        }],
        "fieldConfig": field_config,
        "options": {"colorMode": "background", "graphMode": "none"},
    }


def make_table_panel(title, query, grid_pos, datasource=None):
    """Create a table panel."""
    ds = datasource or PG_DS
    return {
        "id": next_id(),
        "type": "table",
        "title": title,
        "datasource": ds,
        "gridPos": grid_pos,
        "targets": [{
            "datasource": ds,
            "rawSql": query,
            "format": "table",
            "rawQuery": True,
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "green", "value": 0},
                        {"color": "#EAB839", "value": 1},
                        {"color": "red", "value": 2},
                    ],
                },
            },
            "overrides": [{
                "matcher": {"id": "byName", "options": "stoplight"},
                "properties": [
                    {"id": "custom.displayMode", "value": "color-background"},
                    {"id": "custom.width", "value": 80},
                ],
            }],
        },
        "options": {
            "showHeader": True,
            "sortBy": [{"desc": True, "displayName": "stoplight"}],
        },
    }


# ── Template variable builders ─────────────────────────────────────────────

def pg_variable(name, label, query, multi=False):
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": PG_DS,
        "query": query,
        "refresh": 1,
        "multi": multi,
        "includeAll": multi,
        "sort": 1,
    }


def influx_variable(name, label, flux_query, multi=False, hide=0):
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": INFLUX_DS,
        "query": flux_query,
        "refresh": 1,
        "multi": multi,
        "hide": hide,
        "includeAll": multi,
        "sort": 1,
    }


def custom_variable(name, label, options_list, multi=False):
    """Create a custom variable with value:label pairs.
    options_list: list of (value, display_text) tuples.
    """
    query_str = ",".join(f"{text} : {val}" for val, text in options_list)
    opts = [
        {"text": text, "value": val, "selected": False}
        for val, text in options_list
    ]
    if multi:
        all_opt = {"text": "All", "value": "$__all", "selected": True}
        opts.insert(0, all_opt)
        current = {"text": "All", "value": "$__all", "selected": True}
    else:
        if opts:
            opts[0]["selected"] = True
            current = {"text": opts[0]["text"], "value": opts[0]["value"], "selected": True}
        else:
            current = {}
    return {
        "name": name,
        "label": label,
        "type": "custom",
        "query": query_str,
        "options": opts,
        "current": current,
        "refresh": 0,
        "multi": multi,
        "includeAll": multi,
        "sort": 0,
    }


def wrap_dashboard(title, uid, panels, templating, description="", tags=None):
    """Wrap panels into a complete Grafana dashboard model."""
    return {
        "dashboard": {
            "id": None,
            "uid": uid,
            "title": title,
            "description": description,
            "tags": tags or ["auto-generated"],
            "timezone": "browser",
            "schemaVersion": 39,
            "editable": True,
            "graphTooltip": 1,
            "panels": panels,
            "templating": {"list": templating},
            "time": {"from": "now-90d", "to": "now"},
            "refresh": "",
        },
        "overwrite": True,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Dashboard 1: Match Stoplight
# ═══════════════════════════════════════════════════════════════════════════

def build_match_stoplight():
    reset_ids()
    panels = []

    # Summary stats row — each colored to match its meaning
    panels.append(make_stat_panel(
        "Total Warnings",
        "SELECT COUNT(*) FROM metrics WHERE file_name = '${file_name}' AND stoplight >= 1;",
        {"h": 4, "w": 6, "x": 0, "y": 0},
        color="orange",
    ))
    panels.append(make_stat_panel(
        "Red Alerts",
        "SELECT COUNT(*) FROM metrics WHERE file_name = '${file_name}' AND stoplight = 2;",
        {"h": 4, "w": 6, "x": 6, "y": 0},
        color="red",
    ))
    panels.append(make_stat_panel(
        "Yellow Warnings",
        "SELECT COUNT(*) FROM metrics WHERE file_name = '${file_name}' AND stoplight = 1;",
        {"h": 4, "w": 6, "x": 12, "y": 0},
        color="yellow",
    ))
    panels.append(make_stat_panel(
        "Green / OK",
        "SELECT COUNT(*) FROM metrics WHERE file_name = '${file_name}' AND stoplight = 0;",
        {"h": 4, "w": 6, "x": 18, "y": 0},
        color="green",
    ))

    # Traffic light overview — per-group max stoplight
    panels.append({
        "id": next_id(),
        "type": "heywesty-trafficlight-panel",
        "title": "Subsystem Health",
        "datasource": PG_DS,
        "gridPos": {"h": 6, "w": 24, "x": 0, "y": 4},
        "targets": [{
            "datasource": PG_DS,
            "rawSql": """SELECT \"group\" AS metric, MAX(stoplight) AS stoplight
FROM metrics
WHERE file_name = '${file_name}'
GROUP BY \"group\"
ORDER BY \"group\";""",
            "format": "table",
            "rawQuery": True,
            "refId": "A",
        }],
        "fieldConfig": {
            "defaults": {
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "#EAB839", "value": 1},
                        {"color": "red", "value": 2},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "sortLights": True,
            "showValue": False,
            "showTrend": False,
            "greenThreshold": 0,
            "redThreshold": 2,
            "max": 2,
            "invertScale": False,
            "digits": 0,
            "spreadControls": True,
            "renderLink": False,
        },
    })

    # All warnings table
    panels.append(make_table_panel(
        "All Warnings & Alerts",
        '''SELECT "group", metric, value, stoplight
FROM metrics
WHERE file_name = '${file_name}' AND stoplight >= 1
ORDER BY stoplight DESC, "group", metric;''',
        {"h": 8, "w": 24, "x": 0, "y": 10},
    ))

    # Per-group tables in collapsible rows
    groups = [
        "swerve", "flywheel", "turret", "hood",
        "intake_arm", "intake_wheels", "indexer", "chamber", "power", "imu",
    ]
    y = 18
    for group in groups:
        nice = group.replace("_", " ").title()
        inner_panels = [make_table_panel(
            f"{nice} Metrics",
            f'''SELECT metric, value, stoplight
FROM metrics
WHERE file_name = '${{file_name}}' AND "group" = '{group}'
ORDER BY stoplight DESC, metric;''',
            {"h": 8, "w": 24, "x": 0, "y": y + 1},
        )]
        panels.append(make_row_panel(nice, y, collapsed=True, panels=inner_panels))
        y += 1

    # Template variables
    templating = [
        pg_variable("event_key", "Event",
                     "SELECT DISTINCT event_key FROM metrics ORDER BY event_key;"),
        pg_variable("match_info", "Match",
                     "SELECT DISTINCT match_info FROM metrics WHERE event_key = '${event_key}' ORDER BY match_info;"),
        pg_variable("file_name", "Log File",
                     "SELECT DISTINCT file_name FROM metrics WHERE event_key = '${event_key}' AND match_info = '${match_info}' ORDER BY file_name DESC;"),
    ]

    return wrap_dashboard(
        "Match Stoplight",
        "match-stoplight",
        panels,
        templating,
        description="Stoplight health metrics for a specific log file. Select event → match → file.",
        tags=["auto-generated", "stoplight", "match"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# Dashboard 2: Signal Explorer
# ═══════════════════════════════════════════════════════════════════════════

def build_signal_explorer():
    reset_ids()

    # Build full device name mapping for the legend
    name_cases = " else ".join(
        f'if r.device_type + "-" + r.device_id == "{key}" then "{name}"'
        for key, name in DEVICE_NAMES.items()
    )
    name_expr = f"{name_cases} else r.device_type + \"-\" + r.device_id"

    # Main time-series panel driven by variables
    map_line = f'  |> map(fn: (r) => ({{r with _field: ({name_expr}) + " - " + r.signal}}))'
    main_query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: time(v: "${{file_start}}"), stop: time(v: "${{file_end}}"))
  |> filter(fn: (r) => r._measurement == "robot_telemetry")
  |> filter(fn: (r) => r.device_type == "${{device_type}}")
  |> filter(fn: (r) => (r.device_type + "-" + r.device_id) =~ /^(${{device_id:regex}})$/)
  |> filter(fn: (r) => r.signal =~ /^(${{signal:regex}})$/)
  |> filter(fn: (r) => r.file == "${{file}}")
  |> filter(fn: (r) => r._field == "value")
  |> aggregateWindow(every: 200ms, fn: mean, createEmpty: false)
''' + map_line + '''
  |> keep(columns: ["_time", "_value", "_field"])'''

    panels = [
        make_timeseries_panel(
            "Signals: ${device_id:text} / ${signal}",
            main_query,
            {"h": 12, "w": 24, "x": 0, "y": 0},
        ),
    ]

    # Template variables (cascading)
    file_query = f'''import "influxdata/influxdb/schema"
schema.tagValues(bucket: "{INFLUX_BUCKET}", tag: "file", predicate: (r) => r._measurement == "_upload_tracking")'''

    device_type_query = f'''import "influxdata/influxdb/schema"
schema.tagValues(bucket: "{INFLUX_BUCKET}", tag: "device_type", predicate: (r) => r._measurement == "robot_telemetry")'''

    # Build device_id custom variable with composite DeviceType-ID values (unique)
    all_devices = sorted(DEVICE_NAMES.items(), key=lambda x: x[1])  # (key, name) sorted by name

    signal_query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "robot_telemetry")
  |> filter(fn: (r) => r.device_type == "${{device_type}}")
  |> keep(columns: ["signal"])
  |> group()
  |> distinct(column: "signal")'''

    file_start_query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "robot_telemetry" and r.file == "${{file}}" and r._field == "value")
  |> keep(columns: ["_time"])
  |> group()
  |> min(column: "_time")
  |> map(fn: (r) => ({{_value: string(v: r._time)}}))'''

    file_end_query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "robot_telemetry" and r.file == "${{file}}" and r._field == "value")
  |> keep(columns: ["_time"])
  |> group()
  |> max(column: "_time")
  |> map(fn: (r) => ({{_value: string(v: experimental.addDuration(d: 1s, to: r._time))}}))'''

    # Prepend import for experimental (needed by file_end_query)
    file_end_query = 'import "experimental"\n' + file_end_query

    templating = [
        influx_variable("file", "Log File", file_query),
        influx_variable("file_start", "File Start", file_start_query, hide=2),
        influx_variable("file_end", "File End", file_end_query, hide=2),
        influx_variable("device_type", "Device Type", device_type_query),
        custom_variable("device_id", "Device", all_devices, multi=True),
        influx_variable("signal", "Signal", signal_query, multi=True),
    ]

    return wrap_dashboard(
        "Signal Explorer",
        "signal-explorer",
        panels,
        templating,
        description="Explore any raw signal from InfluxDB. Select log file → device type → device ID → signal.",
        tags=["auto-generated", "influxdb", "explorer"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# Dashboard 3: Subsystem Overview
# ═══════════════════════════════════════════════════════════════════════════

def build_subsystem_overview():
    reset_ids()
    panels = []
    y = 0

    file_query = f'''import "influxdata/influxdb/schema"
schema.tagValues(bucket: "{INFLUX_BUCKET}", tag: "file", predicate: (r) => r._measurement == "_upload_tracking")'''

    file_start_query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "robot_telemetry" and r.file == "${{file}}" and r._field == "value")
  |> keep(columns: ["_time"])
  |> group()
  |> min(column: "_time")
  |> map(fn: (r) => ({{_value: string(v: r._time)}}))'''

    file_end_query = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "robot_telemetry" and r.file == "${{file}}" and r._field == "value")
  |> keep(columns: ["_time"])
  |> group()
  |> max(column: "_time")
  |> map(fn: (r) => ({{_value: string(v: experimental.addDuration(d: 1s, to: r._time))}}))'''

    file_end_query = 'import "experimental"\n' + file_end_query

    templating = [
        influx_variable("file", "Log File", file_query),
        influx_variable("file_start", "File Start", file_start_query, hide=2),
        influx_variable("file_end", "File End", file_end_query, hide=2),
    ]

    # ── Swerve Drive ───────────────────────────────────────────────────────
    swerve_panels = []
    sy = y + 1
    for mod_name, ids in SWERVE_MODULES.items():
        abbrev = "".join(w[0] for w in mod_name.split())
        drive_id = ids["drive"]
        steer_id = ids["steer"]
        cc_id = ids["cancoder"]

        swerve_panels.append(make_timeseries_panel(
            f"{abbrev} Drive Current",
            flux_timeseries("TalonFX", drive_id, "StatorCurrent"),
            {"h": 6, "w": 6, "x": 0, "y": sy}, unit="amp",
        ))
        swerve_panels.append(make_timeseries_panel(
            f"{abbrev} Steer Current",
            flux_timeseries("TalonFX", steer_id, "StatorCurrent"),
            {"h": 6, "w": 6, "x": 6, "y": sy}, unit="amp",
        ))
        swerve_panels.append(make_timeseries_panel(
            f"{abbrev} Drive Velocity",
            flux_timeseries("TalonFX", drive_id, "Velocity"),
            {"h": 6, "w": 6, "x": 12, "y": sy}, unit="rot/s",
        ))
        swerve_panels.append(make_timeseries_panel(
            f"{abbrev} Supply Voltage",
            flux_timeseries("TalonFX", drive_id, "SupplyVoltage"),
            {"h": 6, "w": 6, "x": 18, "y": sy}, unit="volt",
        ))
        sy += 6

    panels.append(make_row_panel("Swerve Drive", y, collapsed=True, panels=swerve_panels))
    y += 1

    # ── Left Flywheel ──────────────────────────────────────────────────────
    lfw_panels = []
    lfy = y + 1
    lfw_panels.append(make_timeseries_panel(
        "Left Flywheel Currents (Leader + Follower)",
        flux_multi_device("TalonFX", [4, 5], "StatorCurrent"),
        {"h": 8, "w": 12, "x": 0, "y": lfy}, unit="amp",
    ))
    lfw_panels.append(make_timeseries_panel(
        "Left Flywheel Velocities",
        flux_multi_device("TalonFX", [4, 5], "Velocity"),
        {"h": 8, "w": 12, "x": 12, "y": lfy}, unit="rot/s",
    ))
    panels.append(make_row_panel("Left Flywheel", y, collapsed=True, panels=lfw_panels))
    y += 1

    # ── Right Flywheel ─────────────────────────────────────────────────────
    rfw_panels = []
    rfy = y + 1
    rfw_panels.append(make_timeseries_panel(
        "Right Flywheel Currents (Leader + Follower)",
        flux_multi_device("TalonFX", [19, 18], "StatorCurrent"),
        {"h": 8, "w": 12, "x": 0, "y": rfy}, unit="amp",
    ))
    rfw_panels.append(make_timeseries_panel(
        "Right Flywheel Velocities",
        flux_multi_device("TalonFX", [19, 18], "Velocity"),
        {"h": 8, "w": 12, "x": 12, "y": rfy}, unit="rot/s",
    ))
    panels.append(make_row_panel("Right Flywheel", y, collapsed=True, panels=rfw_panels))
    y += 1

    # ── Left Turret ────────────────────────────────────────────────────────
    lt_panels = []
    lty = y + 1
    lt_panels.append(make_timeseries_panel(
        "Left Turret Motor Current",
        flux_timeseries("TalonFX", 7, "StatorCurrent"),
        {"h": 8, "w": 8, "x": 0, "y": lty}, unit="amp",
    ))
    lt_panels.append(make_timeseries_panel(
        "Left Turret Motor Position",
        flux_timeseries("TalonFX", 7, "Position"),
        {"h": 8, "w": 8, "x": 8, "y": lty},
    ))
    lt_panels.append(make_timeseries_panel(
        "Left Turret Encoder Positions",
        flux_multi_device("CANcoder", [31, 32], "Position"),
        {"h": 8, "w": 8, "x": 16, "y": lty},
    ))
    panels.append(make_row_panel("Left Turret", y, collapsed=True, panels=lt_panels))
    y += 1

    # ── Right Turret ───────────────────────────────────────────────────────
    rt_panels = []
    rty = y + 1
    rt_panels.append(make_timeseries_panel(
        "Right Turret Motor Current",
        flux_timeseries("TalonFX", 16, "StatorCurrent"),
        {"h": 8, "w": 8, "x": 0, "y": rty}, unit="amp",
    ))
    rt_panels.append(make_timeseries_panel(
        "Right Turret Motor Position",
        flux_timeseries("TalonFX", 16, "Position"),
        {"h": 8, "w": 8, "x": 8, "y": rty},
    ))
    rt_panels.append(make_timeseries_panel(
        "Right Turret Encoder Positions",
        flux_multi_device("CANcoder", [41, 42], "Position"),
        {"h": 8, "w": 8, "x": 16, "y": rty},
    ))
    panels.append(make_row_panel("Right Turret", y, collapsed=True, panels=rt_panels))
    y += 1

    # ── Left Hood ──────────────────────────────────────────────────────────
    lh_panels = []
    lhy = y + 1
    lh_panels.append(make_timeseries_panel(
        "Left Hood Current",
        flux_timeseries("TalonFX", 8, "StatorCurrent"),
        {"h": 8, "w": 12, "x": 0, "y": lhy}, unit="amp",
    ))
    lh_panels.append(make_timeseries_panel(
        "Left Hood Position",
        flux_timeseries("TalonFX", 8, "Position"),
        {"h": 8, "w": 12, "x": 12, "y": lhy},
    ))
    panels.append(make_row_panel("Left Hood", y, collapsed=True, panels=lh_panels))
    y += 1

    # ── Right Hood ─────────────────────────────────────────────────────────
    rh_panels = []
    rhy = y + 1
    rh_panels.append(make_timeseries_panel(
        "Right Hood Current",
        flux_timeseries("TalonFX", 9, "StatorCurrent"),
        {"h": 8, "w": 12, "x": 0, "y": rhy}, unit="amp",
    ))
    rh_panels.append(make_timeseries_panel(
        "Right Hood Position",
        flux_timeseries("TalonFX", 9, "Position"),
        {"h": 8, "w": 12, "x": 12, "y": rhy},
    ))
    panels.append(make_row_panel("Right Hood", y, collapsed=True, panels=rh_panels))
    y += 1

    # ── Intake ─────────────────────────────────────────────────────────────
    intake_panels = []
    iy = y + 1
    intake_panels.append(make_timeseries_panel(
        "Intake Arm Current",
        flux_timeseries("TalonFX", 15, "StatorCurrent"),
        {"h": 8, "w": 8, "x": 0, "y": iy}, unit="amp",
    ))
    intake_panels.append(make_timeseries_panel(
        "Intake Arm Position (CANcoder)",
        flux_timeseries("CANcoder", 15, "Position"),
        {"h": 8, "w": 8, "x": 8, "y": iy},
    ))
    intake_panels.append(make_timeseries_panel(
        "Intake Wheels Current",
        flux_timeseries("TalonFX", 14, "StatorCurrent"),
        {"h": 8, "w": 8, "x": 16, "y": iy}, unit="amp",
    ))
    panels.append(make_row_panel("Intake", y, collapsed=True, panels=intake_panels))
    y += 1

    # ── Indexer + Chamber ──────────────────────────────────────────────────
    ic_panels = []
    icy = y + 1
    ic_panels.append(make_timeseries_panel(
        "Indexer Current",
        flux_timeseries("TalonFX", 13, "StatorCurrent"),
        {"h": 8, "w": 6, "x": 0, "y": icy}, unit="amp",
    ))
    ic_panels.append(make_timeseries_panel(
        "Left Chamber Current",
        flux_timeseries("TalonFX", 6, "StatorCurrent"),
        {"h": 8, "w": 6, "x": 6, "y": icy}, unit="amp",
    ))
    ic_panels.append(make_timeseries_panel(
        "Right Chamber Current",
        flux_timeseries("TalonFX", 17, "StatorCurrent"),
        {"h": 8, "w": 6, "x": 12, "y": icy}, unit="amp",
    ))
    ic_panels.append(make_timeseries_panel(
        "Indexer + Chamber Velocity",
        flux_multi_device("TalonFX", [13, 6, 17], "Velocity"),
        {"h": 8, "w": 6, "x": 18, "y": icy}, unit="rot/s",
    ))
    panels.append(make_row_panel("Indexer + Chamber", y, collapsed=True, panels=ic_panels))
    y += 1

    # ── Power ──────────────────────────────────────────────────────────────
    all_talon_ids = set()
    for mod in SWERVE_MODULES.values():
        all_talon_ids.add(mod["drive"])
        all_talon_ids.add(mod["steer"])
    for tid in [15, 14, 13, 6, 17, 7, 16, 8, 9, 4, 5, 19, 18]:
        all_talon_ids.add(tid)

    pwr_panels = []
    py_ = y + 1
    # Use FL drive for bus voltage (representative)
    pwr_panels.append(make_timeseries_panel(
        "Bus Voltage (FL Drive)",
        flux_timeseries("TalonFX", 23, "SupplyVoltage"),
        {"h": 8, "w": 12, "x": 0, "y": py_}, unit="volt",
    ))
    pwr_panels.append(make_timeseries_panel(
        "All Motor Supply Currents",
        flux_multi_device("TalonFX", sorted(all_talon_ids), "SupplyCurrent"),
        {"h": 8, "w": 12, "x": 12, "y": py_}, unit="amp",
    ))
    panels.append(make_row_panel("Power", y, collapsed=True, panels=pwr_panels))
    y += 1

    # ── IMU ────────────────────────────────────────────────────────────────
    imu_panels = []
    imy = y + 1
    for i, sig in enumerate(PIGEON_SIGNALS):
        imu_panels.append(make_timeseries_panel(
            f"Pigeon {sig}",
            flux_timeseries("Pigeon2", 0, sig),
            {"h": 8, "w": 6, "x": i * 6, "y": imy}, unit="deg" if sig != "AngularVelocityZWorld" else "deg/s",
        ))
    panels.append(make_row_panel("IMU (Pigeon2)", y, collapsed=True, panels=imu_panels))
    y += 1

    return wrap_dashboard(
        "Subsystem Overview",
        "subsystem-overview",
        panels,
        templating,
        description="Pre-built time-series panels for every subsystem. Select a log file, then expand subsystem rows.",
        tags=["auto-generated", "influxdb", "subsystem"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate Grafana dashboards")
    parser.add_argument("--upload", action="store_true", help="Upload to Grafana API")
    parser.add_argument("--grafana-url", default="http://localhost:3000", help="Grafana URL")
    parser.add_argument("--grafana-user", default="admin")
    parser.add_argument("--grafana-pass", default="admin")
    args = parser.parse_args()

    os.makedirs(DASHBOARD_DIR, exist_ok=True)

    dashboards = {
        "match_stoplight.json": build_match_stoplight(),
        "signal_explorer.json": build_signal_explorer(),
        "subsystem_overview.json": build_subsystem_overview(),
    }

    for filename, dashboard in dashboards.items():
        path = os.path.join(DASHBOARD_DIR, filename)
        with open(path, "w") as f:
            json.dump(dashboard, f, indent=2)
        print(f"  Wrote {path}")

    if args.upload:
        import urllib.request
        import base64

        creds = base64.b64encode(f"{args.grafana_user}:{args.grafana_pass}".encode()).decode()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {creds}",
        }

        for filename, dashboard in dashboards.items():
            data = json.dumps(dashboard).encode("utf-8")
            req = urllib.request.Request(
                f"{args.grafana_url}/api/dashboards/db",
                data=data,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    result = json.loads(resp.read())
                    print(f"  Uploaded {filename}: {result.get('url', 'ok')}")
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                print(f"  FAILED {filename}: {e.code} {body}", file=sys.stderr)

    print("\nDone! Dashboards available at:")
    print(f"  Match Stoplight:    {args.grafana_url if args.upload else 'http://localhost:3000'}/d/match-stoplight")
    print(f"  Signal Explorer:    {args.grafana_url if args.upload else 'http://localhost:3000'}/d/signal-explorer")
    print(f"  Subsystem Overview: {args.grafana_url if args.upload else 'http://localhost:3000'}/d/subsystem-overview")


if __name__ == "__main__":
    main()
