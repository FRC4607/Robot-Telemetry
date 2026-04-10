# Pit Logs Retriever — FRC Team 4607

Automatically retrieves `.hoot` log files from the RoboRIO over SSH/SFTP, uploads them to the cloud telemetry API, caches locally, and deletes from the RoboRIO once safe.

Designed to run on a dedicated device in the pit (e.g. a small laptop or Raspberry Pi) with a browser in kiosk mode showing the status page.

## Status Display

The app serves a full-screen status page at `http://localhost:5000`:

| Color | Meaning |
|-------|---------|
| **Dark blue** | Waiting for robot connection |
| **Red** | Transferring files — **DO NOT** unplug the robot |
| **Green** | All files transferred — safe to unplug / power down |

## Configuration

Key constants are defined at the top of `app.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBORIO_IP` | `10.46.7.2` | RoboRIO IP address (team 4607) |
| `ROBORIO_LOG_DIR` | `/mnt/sda` | Directory on the RoboRIO where `.hoot` files are stored |
| `CLOUD_API_URL` | `https://telemetry.beckerrobotics.com/api/upload` | Upload endpoint |
| `LOCAL_CACHE_DIR` | `/home/cis/logs-cache` | Local cache directory for downloaded logs |

## Setup

```bash
cd pit-logfiles-retriever-app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000 in a browser. For a pit kiosk display, use Chromium in kiosk mode:

```bash
chromium-browser --kiosk http://localhost:5000
```

## How It Works

1. A background worker thread polls the RoboRIO over SSH every few seconds.
2. When connected, it scans `/mnt/sda` for `.hoot` files.
3. Each file is downloaded via SFTP to a temp directory, then uploaded to the cloud API.
4. On successful upload, the file is cached locally and deleted from the RoboRIO.
5. The browser page auto-refreshes status via `/api/status` and updates the display color and message in real time.

## Dependencies

- **Flask** — Web server for the status page
- **Paramiko** — SSH/SFTP connection to the RoboRIO
- **requests / requests-toolbelt** — Multipart file upload with progress tracking
