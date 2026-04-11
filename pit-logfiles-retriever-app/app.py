#!/usr/bin/env python3
"""
Pit Logs Retriever — FRC Team 4607
Automatically retrieves .hoot log files from the RoboRIO over SSH/SFTP,
uploads them to the cloud telemetry API, caches locally, and deletes from
the RoboRIO once safe.

Displays a full-screen status page:
    RED    — Transferring files, DO NOT unplug the robot
    GREEN  — All files transferred, safe to unplug / power down
    DARK   — Waiting for robot connection

Setup (on the pit device):
    cd pit-logfiles-retriever-app
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python app.py

    Then open http://localhost:5000 in a browser (Chromium kiosk mode
    recommended: chromium-browser --kiosk http://localhost:5000 ).
"""

import glob
import logging
import os
import shutil
import stat as stat_module
import subprocess
import tempfile
import threading
import time

import paramiko
import requests
from flask import Flask, jsonify
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor

# ── Configuration ──────────────────────────────────────────────────────────
ROBORIO_IP = "10.46.7.2"
ROBORIO_PORT = 22
ROBORIO_USER = "admin"
ROBORIO_PASSWORD = ""
ROBORIO_LOG_DIR = "/media"

CLOUD_API_URL = "https://telemetry.beckerrobotics.com/api/upload"

LOCAL_CACHE_DIR = "/home/cis/logs-cache"
LOCAL_PENDING_DIR = os.path.join(LOCAL_CACHE_DIR, "pending")
LOCAL_REJECTED_DIR = os.path.join(LOCAL_CACHE_DIR, "rejected")

CONNECT_POLL_SEC = 3  # seconds between connection attempts
RESCAN_POLL_SEC = 5  # seconds between re-scans while connected
UPLOAD_RETRIES = 3  # retry count for cloud uploads
UPLOAD_RETRY_DELAY = 2  # seconds between upload retries
PENDING_RETRY_SEC = 60  # seconds between pending-upload retry sweeps

WEB_HOST = "0.0.0.0"
WEB_PORT = 5000

USB_MOUNT_BASE = "/mnt/pitusb"
USB_POLL_SEC = 3  # seconds between USB drive checks

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pit-retriever")


# Suppress Werkzeug request logging for the polling endpoint
class _QuietStatusFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return "/api/status" not in msg


logging.getLogger("werkzeug").addFilter(_QuietStatusFilter())

# ── Flask ──────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Shared state ───────────────────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "state": "waiting",  # waiting | transferring | complete | error
    "message": "Waiting for robot connection\u2026",
    "phase": "",  # downloading | uploading | ""
    "current_file": "",
    "files_total": 0,
    "files_completed": 0,
    "file_bytes_done": 0,
    "file_bytes_total": 0,
    "speed_bps": 0.0,
    "error": "",
}

_usb_lock = threading.Lock()
_usb_state = {
    "usb_status": "disconnected",  # disconnected | mounted | writing | done
    "usb_message": "",
    "usb_files_total": 0,
    "usb_files_done": 0,
    "usb_bytes_done": 0,
    "usb_bytes_total": 0,
}


def _set(**kw):
    with _lock:
        _state.update(kw)


def _get():
    with _lock:
        d = dict(_state)
    with _usb_lock:
        d.update(_usb_state)
    return d


def _usb_set(**kw):
    with _usb_lock:
        _usb_state.update(kw)


# ── Speed tracker ──────────────────────────────────────────────────────────
class _SpeedTracker:
    """Sliding-window speed calculation."""

    def __init__(self, window: float = 2.0):
        self._samples: list[tuple[float, int]] = []
        self._window = window

    def update(self, bytes_done: int):
        now = time.monotonic()
        self._samples.append((now, bytes_done))
        cutoff = now - self._window
        self._samples = [(t, b) for t, b in self._samples if t >= cutoff]

    @property
    def speed(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        t0, b0 = self._samples[0]
        t1, b1 = self._samples[-1]
        dt = t1 - t0
        return (b1 - b0) / dt if dt > 0 else 0.0

    def reset(self):
        self._samples.clear()


# ── SFTP helpers ───────────────────────────────────────────────────────────
def _sftp_find_hoot_files(sftp, remote_dir):
    """Recursively yield (remote_path, filename, size) for .hoot files."""
    results = []
    try:
        entries = sftp.listdir_attr(remote_dir)
    except IOError:
        return results
    for entry in entries:
        path = f"{remote_dir}/{entry.filename}"
        mode = entry.st_mode
        if mode is not None and stat_module.S_ISDIR(mode):
            results.extend(_sftp_find_hoot_files(sftp, path))
        elif entry.filename.endswith(".hoot"):
            results.append((path, entry.filename, entry.st_size or 0))
    return results


def _filter_stable_files(sftp, hoot_files, delay=2):
    """Return only files whose size hasn't changed over a short delay,
    indicating they are not actively being written to."""
    if not hoot_files:
        return []
    time.sleep(delay)
    stable = []
    for remote_path, filename, size1 in hoot_files:
        try:
            attr = sftp.stat(remote_path)
            size2 = attr.st_size or 0
        except IOError:
            continue  # file disappeared
        if size1 == size2:
            stable.append((remote_path, filename, size2))
        else:
            log.info(
                "Skipping %s — file is still being written (%d → %d bytes)",
                filename,
                size1,
                size2,
            )
    return stable


# ── Download manifest ──────────────────────────────────────────────────────
_MANIFEST_PATH = os.path.join(LOCAL_CACHE_DIR, "downloaded.manifest")
_manifest_set: set[str] = set()
_manifest_loaded = False


def _load_manifest():
    """Load the set of previously-downloaded filenames from disk (once)."""
    global _manifest_loaded
    if _manifest_loaded:
        return
    try:
        with open(_MANIFEST_PATH, "r") as f:
            for line in f:
                name = line.strip()
                if name:
                    _manifest_set.add(name)
    except FileNotFoundError:
        pass
    _manifest_loaded = True
    log.info("Manifest loaded: %d previously-downloaded file(s)", len(_manifest_set))


def _mark_downloaded(filename):
    """Record a filename in the persistent manifest."""
    if filename in _manifest_set:
        return
    _manifest_set.add(filename)
    try:
        with open(_MANIFEST_PATH, "a") as f:
            f.write(filename + "\n")
    except OSError as exc:
        log.warning("Could not write manifest: %s", exc)


def _is_already_downloaded(filename):
    """Return True if we have already downloaded this file before."""
    _load_manifest()
    return (
        filename in _manifest_set
        or os.path.isfile(os.path.join(LOCAL_CACHE_DIR, filename))
        or os.path.isfile(os.path.join(LOCAL_PENDING_DIR, filename))
        or os.path.isfile(os.path.join(LOCAL_REJECTED_DIR, filename))
    )


# ── Upload helper ──────────────────────────────────────────────────────────
def _upload_file(filepath, filename, speed_tracker):
    """Upload to cloud API with progress tracking.

    Returns ``"ok"`` on success, ``"rejected"`` when the server
    permanently rejected the file (4xx), or ``"failed"`` for transient
    errors worth retrying later.
    """
    for attempt in range(1, UPLOAD_RETRIES + 1):
        try:
            with open(filepath, "rb") as f:
                encoder = MultipartEncoder(
                    fields={"file": (filename, f, "application/octet-stream")}
                )
                total_len = encoder.len

                def _cb(monitor, _st=speed_tracker, _tl=total_len):
                    _st.update(monitor.bytes_read)
                    _set(
                        file_bytes_done=monitor.bytes_read,
                        file_bytes_total=_tl,
                        speed_bps=_st.speed,
                    )

                monitor = MultipartEncoderMonitor(encoder, _cb)
                resp = requests.post(
                    CLOUD_API_URL,
                    data=monitor,
                    headers={"Content-Type": monitor.content_type},
                    timeout=300,
                )
            if resp.status_code == 200:
                return "ok"
            log.warning(
                "Upload attempt %d/%d for %s returned %d: %s",
                attempt,
                UPLOAD_RETRIES,
                filename,
                resp.status_code,
                resp.text[:200],
            )
            # 4xx = permanent rejection (bad file, invalid name, etc.)
            if 400 <= resp.status_code < 500:
                return "rejected"
        except Exception as exc:
            log.warning(
                "Upload attempt %d/%d for %s failed: %s",
                attempt,
                UPLOAD_RETRIES,
                filename,
                exc,
            )
        if attempt < UPLOAD_RETRIES:
            time.sleep(UPLOAD_RETRY_DELAY)
    return "failed"


# ── Worker thread ──────────────────────────────────────────────────────────
def _worker():
    os.makedirs(LOCAL_CACHE_DIR, exist_ok=True)
    os.makedirs(LOCAL_PENDING_DIR, exist_ok=True)
    os.makedirs(LOCAL_REJECTED_DIR, exist_ok=True)

    # Backfill manifest with any pre-existing files on disk
    _load_manifest()
    for d in (LOCAL_CACHE_DIR, LOCAL_PENDING_DIR, LOCAL_REJECTED_DIR):
        try:
            for fn in os.listdir(d):
                if fn.endswith(".hoot"):
                    _mark_downloaded(fn)
        except OSError:
            pass

    while True:
        ssh = None
        sftp = None
        try:
            _set(
                state="waiting",
                message="Waiting for robot connection\u2026",
                phase="",
                current_file="",
                files_total=0,
                files_completed=0,
                file_bytes_done=0,
                file_bytes_total=0,
                speed_bps=0.0,
                error="",
            )

            # ── Connect ───────────────────────────────────────────────
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                ROBORIO_IP,
                port=ROBORIO_PORT,
                username=ROBORIO_USER,
                password=ROBORIO_PASSWORD,
                look_for_keys=False,
                allow_agent=False,
                timeout=5,
                banner_timeout=10,
            )
            transport = ssh.get_transport()
            if transport:
                transport.set_keepalive(2)  # send SSH keepalive every 2s
            sftp = ssh.open_sftp()
            log.info("Connected to RoboRIO at %s", ROBORIO_IP)

            # ── Scan / transfer loop while connected ──────────────────
            while True:
                transport = ssh.get_transport()
                if not transport or not transport.is_active():
                    break

                _set(message="Connected \u2014 scanning for log files\u2026")
                hoot_files = _sftp_find_hoot_files(sftp, ROBORIO_LOG_DIR)
                new_files = [
                    (rp, fn, sz)
                    for rp, fn, sz in hoot_files
                    if not _is_already_downloaded(fn)
                ]
                # Only transfer files that aren't actively being written to
                new_files = _filter_stable_files(sftp, new_files)

                if new_files:
                    _do_transfer(sftp, new_files)

                # ── Green state ───────────────────────────────────────
                completed = _get()["files_completed"]
                total = _get()["files_total"]
                msg = "No new log files found. Safe to unplug."
                if total > 0:
                    msg = f"{completed}/{total} file(s) transferred. Safe to unplug."
                _set(
                    state="complete",
                    message=msg,
                    phase="",
                    current_file="",
                    speed_bps=0.0,
                )
                log.info("Status: %s", msg)

                # Wait then verify the connection is still alive
                time.sleep(RESCAN_POLL_SEC)
                transport = ssh.get_transport()
                if not transport or not transport.is_active():
                    log.info("Robot disconnected (transport inactive)")
                    break
                try:
                    transport.send_ignore()
                except (paramiko.SSHException, OSError, EOFError):
                    log.info("Robot disconnected (keepalive failed)")
                    break

        except (paramiko.SSHException, OSError, TimeoutError):
            pass  # normal when robot isn't reachable
        except Exception as exc:
            log.error("Unexpected error in worker: %s", exc)
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
            if ssh:
                try:
                    ssh.close()
                except Exception:
                    pass

        time.sleep(CONNECT_POLL_SEC)


def _do_transfer(sftp, new_files):
    """Download files from the RoboRIO, then upload to cloud after going green."""
    total = len(new_files)
    log.info("Found %d new file(s) to transfer", total)

    _set(
        state="transferring",
        message=f"Found {total} log file(s) to transfer",
        files_total=total,
        files_completed=0,
    )

    speed = _SpeedTracker()
    downloaded = []  # list of (tmp_path, filename, remote_path)

    # ── Phase 1: Download all files from RoboRIO (robot must stay plugged in) ──
    for idx, (remote_path, filename, file_size) in enumerate(new_files):
        file_num = idx + 1

        log.info(
            "Downloading [%d/%d]: %s (%d bytes)", file_num, total, filename, file_size
        )
        speed.reset()
        _set(
            phase="downloading",
            current_file=filename,
            file_bytes_done=0,
            file_bytes_total=file_size,
            speed_bps=0.0,
            message=f"Downloading {file_num}/{total}: {filename}",
        )

        tmp_path = os.path.join(LOCAL_CACHE_DIR, f".{filename}.tmp")
        try:

            def _dl_cb(done, tot, _s=speed):
                _s.update(done)
                _set(file_bytes_done=done, file_bytes_total=tot, speed_bps=_s.speed)

            sftp.get(remote_path, tmp_path, callback=_dl_cb)
        except Exception as exc:
            log.error("Download failed for %s: %s", filename, exc)
            _safe_remove(tmp_path)
            _set(error=f"Download failed: {filename}")
            continue

        downloaded.append((tmp_path, filename, remote_path))
        _mark_downloaded(filename)
        _set(files_completed=file_num)

    # ── Queue cloud uploads for the background uploader thread ──
    for tmp_path, filename, remote_path in downloaded:
        pending_path = os.path.join(LOCAL_PENDING_DIR, filename)
        try:
            os.replace(tmp_path, pending_path)
        except OSError:
            log.warning("Could not move %s to pending", filename)

    log.info("Downloads complete — %d file(s) queued for cloud upload", len(downloaded))


def _safe_remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ── USB flash drive thread ────────────────────────────────────────────────
def _find_usb_device():
    """Return the device path of a removable USB block device, or None."""
    try:
        for dev in glob.glob("/sys/block/sd*"):
            removable = os.path.join(dev, "removable")
            if os.path.isfile(removable):
                with open(removable) as f:
                    if f.read().strip() == "1":
                        name = os.path.basename(dev)
                        # Find the first partition, or use the raw device
                        parts = sorted(glob.glob(f"/dev/{name}[0-9]*"))
                        return parts[0] if parts else f"/dev/{name}"
    except OSError:
        pass
    return None


def _is_mounted(device):
    """Check if a device is currently mounted."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                if line.startswith(device + " "):
                    return True
    except OSError:
        pass
    return False


def _mount_usb(device):
    """Mount the USB device at USB_MOUNT_BASE. Returns True on success."""
    os.makedirs(USB_MOUNT_BASE, exist_ok=True)
    uid = os.getuid()
    gid = os.getgid()
    # Try with uid/gid options first (works for FAT/exFAT/NTFS)
    for opts in [f"uid={uid},gid={gid}", None]:
        try:
            cmd = ["sudo", "mount"]
            if opts:
                cmd += ["-o", opts]
            cmd += [device, USB_MOUNT_BASE]
            subprocess.run(cmd, check=True, capture_output=True, timeout=10)
            log.info("Mounted USB %s at %s", device, USB_MOUNT_BASE)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if opts is None:
                log.warning("Failed to mount USB %s", device)
    return False


def _unmount_usb():
    """Unmount USB_MOUNT_BASE if mounted."""
    try:
        subprocess.run(
            ["sudo", "umount", USB_MOUNT_BASE],
            check=True,
            capture_output=True,
            timeout=10,
        )
        log.info("Unmounted %s", USB_MOUNT_BASE)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass


def _usb_worker():
    """Detect USB flash drive, mount it, copy cached .hoot files to it."""
    last_device = None

    while True:
        time.sleep(USB_POLL_SEC)

        device = _find_usb_device()

        if device is None:
            if last_device is not None:
                # Drive was removed
                _unmount_usb()
                log.info("USB drive removed")
            last_device = None
            _usb_set(
                usb_status="disconnected",
                usb_message="",
                usb_files_total=0,
                usb_files_done=0,
                usb_bytes_done=0,
                usb_bytes_total=0,
            )
            continue

        # Drive detected — mount if needed
        if not _is_mounted(device):
            if not _mount_usb(device):
                _usb_set(usb_status="disconnected", usb_message="Mount failed")
                last_device = device
                continue

        last_device = device

        # Find .hoot files in cache + pending that aren't already on the USB
        all_hoots = []
        for d in (LOCAL_CACHE_DIR, LOCAL_PENDING_DIR):
            try:
                for fn in os.listdir(d):
                    if fn.endswith(".hoot") and os.path.isfile(os.path.join(d, fn)):
                        all_hoots.append((os.path.join(d, fn), fn))
            except OSError:
                continue

        # Deduplicate by filename (cache takes priority)
        seen = set()
        unique_hoots = []
        for filepath, fn in all_hoots:
            if fn not in seen:
                seen.add(fn)
                unique_hoots.append((filepath, fn))

        # Filter out files already on the USB
        to_copy = [
            (fp, fn)
            for fp, fn in unique_hoots
            if not os.path.isfile(os.path.join(USB_MOUNT_BASE, fn))
        ]

        if not to_copy:
            _usb_set(
                usb_status="done",
                usb_message="All files on USB. Safe to remove.",
                usb_files_total=0,
                usb_files_done=0,
            )
            continue

        # Copy files
        total = len(to_copy)
        total_bytes = sum(os.path.getsize(fp) for fp, _ in to_copy)
        bytes_done = 0
        copied = 0
        log.info("USB: copying %d file(s) to %s", total, USB_MOUNT_BASE)
        _usb_set(
            usb_status="writing",
            usb_message=f"Copying {total} file(s) to USB\u2026",
            usb_files_total=total,
            usb_files_done=0,
            usb_bytes_total=total_bytes,
            usb_bytes_done=0,
        )

        for idx, (filepath, fn) in enumerate(to_copy):
            dest = os.path.join(USB_MOUNT_BASE, fn)
            file_size = os.path.getsize(filepath)
            _usb_set(
                usb_message=f"Copying {idx + 1}/{total}: {fn}",
            )
            try:
                subprocess.run(
                    ["sudo", "cp", filepath, dest],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                bytes_done += file_size
                copied += 1
                _usb_set(
                    usb_bytes_done=bytes_done,
                    usb_files_done=copied,
                )
                log.info("USB: copied %s", fn)
            except subprocess.CalledProcessError as exc:
                log.warning("USB: failed to copy %s: %s", fn, exc)
                # Drive may have been yanked
                if not os.path.ismount(USB_MOUNT_BASE):
                    _usb_set(usb_status="disconnected", usb_message="")
                    break
            except subprocess.TimeoutExpired:
                log.warning("USB: copy timed out for %s", fn)
            except OSError as exc:
                log.warning("USB: failed to copy %s: %s", fn, exc)

        # Sync to ensure data is flushed to the drive
        try:
            subprocess.run(["sudo", "sync"], timeout=30, capture_output=True)
        except (subprocess.TimeoutExpired, OSError):
            pass

        if os.path.ismount(USB_MOUNT_BASE):
            if copied == total:
                _usb_set(
                    usb_status="done",
                    usb_message="All files on USB. Safe to remove.",
                    usb_files_done=copied,
                )
                log.info("USB: all files copied, safe to remove")
            elif copied > 0:
                _usb_set(
                    usb_status="done",
                    usb_message=f"{copied}/{total} copied. Safe to remove.",
                    usb_files_done=copied,
                )
                log.warning("USB: %d/%d files copied (some failed)", copied, total)
            else:
                _usb_set(
                    usb_status="mounted",
                    usb_message=f"Write failed — check permissions",
                )
                log.error("USB: all copies failed — permission denied?")


# ── Cloud upload thread ────────────────────────────────────────────────────
def _pending_retry_worker():
    """Upload files from the pending directory to the cloud API."""
    os.makedirs(LOCAL_PENDING_DIR, exist_ok=True)
    os.makedirs(LOCAL_REJECTED_DIR, exist_ok=True)
    speed = _SpeedTracker()

    while True:
        time.sleep(5)  # check frequently for newly queued files
        try:
            pending = [
                f
                for f in os.listdir(LOCAL_PENDING_DIR)
                if f.endswith(".hoot")
                and os.path.isfile(os.path.join(LOCAL_PENDING_DIR, f))
            ]
        except OSError:
            continue

        if not pending:
            continue

        log.info("Cloud upload: %d file(s) to upload", len(pending))
        for filename in pending:
            filepath = os.path.join(LOCAL_PENDING_DIR, filename)
            speed.reset()
            result = _upload_file(filepath, filename, speed)
            if result == "ok":
                cache_path = os.path.join(LOCAL_CACHE_DIR, filename)
                try:
                    os.replace(filepath, cache_path)
                except OSError:
                    pass
                log.info("Cloud upload succeeded: %s", filename)
            elif result == "rejected":
                rejected_path = os.path.join(LOCAL_REJECTED_DIR, filename)
                try:
                    os.replace(filepath, rejected_path)
                except OSError:
                    pass
                log.warning("Cloud upload rejected: %s — moved to rejected/", filename)
            else:
                log.warning("Cloud upload failed: %s — will retry later", filename)


# ── Flask routes ───────────────────────────────────────────────────────────
@app.route("/")
def index():
    return _HTML_PAGE


@app.route("/api/status")
def api_status():
    return jsonify(_get())


# ── Embedded HTML / CSS / JS ───────────────────────────────────────────────
_HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pit Logs Retriever — FRC 4607</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  min-height:100vh;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  text-align:center;padding:2rem;
  transition:background-color .5s ease,color .3s ease;
}
body.waiting   {background:#0f172a;color:#94a3b8}
body.transferring{background:#dc2626;color:#fff}
body.complete  {background:#16a34a;color:#fff}
body.error     {background:#d97706;color:#fff}

.header{position:fixed;top:1rem;font-size:1.2rem;opacity:.6;letter-spacing:.15em;text-transform:uppercase}
.icon{font-size:8rem;line-height:1;margin-bottom:.5rem;min-height:160px;display:flex;align-items:center;justify-content:center}
.icon svg{width:200px;height:160px}
.status{font-size:3.5rem;font-weight:800;margin-bottom:.3rem}
.message{font-size:1.8rem;margin-bottom:1.5rem;opacity:.9;min-height:2.2rem}

.bar-wrap{
  width:min(80vw,700px);height:28px;
  background:rgba(0,0,0,.25);border-radius:14px;
  overflow:hidden;margin-bottom:.8rem;
}
.bar{height:100%;background:rgba(255,255,255,.85);border-radius:14px;width:0%;transition:width .35s ease}

.speed{font-size:2.6rem;font-weight:700;min-height:3.2rem}
.detail{font-size:1.3rem;opacity:.75;min-height:1.6rem}
.phase{font-size:1.1rem;opacity:.6;margin-top:.3rem;min-height:1.4rem}

.hidden{display:none}

@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
body.transferring .icon{animation:pulse 1.6s ease-in-out infinite}

@keyframes glow{0%,100%{filter:drop-shadow(0 0 8px rgba(255,255,255,.4))}50%{filter:drop-shadow(0 0 20px rgba(255,255,255,.7))}}
body.complete .icon{animation:glow 3s ease-in-out infinite}

.usb-badge{
  position:fixed;bottom:1.2rem;right:1.2rem;
  display:flex;align-items:center;gap:.5rem;
  padding:.5rem .9rem;border-radius:10px;
  font-size:.95rem;font-weight:600;
  background:rgba(0,0,0,.35);color:#94a3b8;
  transition:background .3s,color .3s;
}
.usb-badge svg{width:22px;height:22px;flex-shrink:0}
.usb-badge.disconnected{opacity:.4}
.usb-badge.mounted{background:rgba(0,0,0,.35);color:#94a3b8}
.usb-badge.writing{background:rgba(234,179,8,.25);color:#fbbf24}
.usb-badge.done{background:rgba(34,197,94,.25);color:#4ade80}
@keyframes usb-pulse{0%,100%{opacity:1}50%{opacity:.5}}
.usb-badge.writing{animation:usb-pulse 1.2s ease-in-out infinite}
.usb-bar-wrap{width:80px;height:6px;background:rgba(255,255,255,.15);border-radius:3px;overflow:hidden}
.usb-bar{height:100%;background:currentColor;border-radius:3px;width:0%;transition:width .3s ease}
</style>
</head>
<body class="waiting">

<div class="header">FRC 4607 &bull; PIT LOGS RETRIEVER</div>

<div class="icon" id="icon">
<svg id="eth-svg" viewBox="0 0 200 160" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <!-- Clip: plug visible from socket opening top (y=22) downward -->
    <clipPath id="plug-clip">
      <rect x="82" y="22" width="36" height="140"/>
    </clipPath>
  </defs>

  <!-- Jack housing — drawn FIRST so plug can overlap it -->
  <rect x="68" y="8" width="64" height="52" rx="6" fill="#334155" stroke="#64748b" stroke-width="2"/>
  <!-- Socket opening (dark hole) -->
  <rect x="82" y="22" width="36" height="30" rx="2" fill="#0f172a" stroke="#475569" stroke-width="1.5"/>
  <!-- Internal contacts inside jack (visible when plug is out) -->
  <line x1="89" y1="38" x2="89" y2="50" stroke="#475569" stroke-width="1.2" opacity="0.6"/>
  <line x1="93" y1="38" x2="93" y2="50" stroke="#475569" stroke-width="1.2" opacity="0.6"/>
  <line x1="97" y1="38" x2="97" y2="50" stroke="#475569" stroke-width="1.2" opacity="0.6"/>
  <line x1="101" y1="38" x2="101" y2="50" stroke="#475569" stroke-width="1.2" opacity="0.6"/>
  <line x1="105" y1="38" x2="105" y2="50" stroke="#475569" stroke-width="1.2" opacity="0.6"/>
  <line x1="109" y1="38" x2="109" y2="50" stroke="#475569" stroke-width="1.2" opacity="0.6"/>

  <!-- RJ45 plug — drawn AFTER housing so it overlaps the socket bottom border -->
  <g clip-path="url(#plug-clip)">
    <g id="plug">
      <!-- Cable -->
      <rect x="95" y="88" width="10" height="200" rx="5" fill="#64748b"/>
      <!-- Plug body -->
      <rect x="84" y="55" width="32" height="36" rx="3" fill="#94a3b8" stroke="#cbd5e1" stroke-width="1.5"/>
      <!-- Clip / latch tab -->
      <rect x="93" y="52" width="14" height="6" rx="2" fill="#cbd5e1"/>

      <!-- Plug slides from below up into the jack, pauses, then withdraws -->
      <animateTransform attributeName="transform" type="translate"
        values="0,60; 0,60; 0,-20; 0,-20; 0,-20; 0,-20; 0,60"
        keyTimes="0; 0.05; 0.25; 0.65; 0.85; 0.92; 1"
        dur="4s" repeatCount="indefinite"/>
    </g>
  </g>

  <!-- LEDs — top corners of housing, always visible -->
  <circle id="led-left" cx="75" cy="16" r="3.5" fill="#1e293b" stroke="#475569" stroke-width="0.8"/>
  <circle id="led-right" cx="125" cy="16" r="3.5" fill="#1e293b" stroke="#475569" stroke-width="0.8"/>

  <!-- LED animations — light up after plug is fully inserted -->
  <animate xlink:href="#led-left" attributeName="fill"
    values="#1e293b;#1e293b;#22c55e;#22c55e;#22c55e;#22c55e;#1e293b"
    keyTimes="0; 0.28; 0.32; 0.65; 0.88; 0.92; 1"
    dur="4s" repeatCount="indefinite"/>
  <animate xlink:href="#led-right" attributeName="fill"
    values="#1e293b;#1e293b;#f59e0b;#1e293b;#f59e0b;#1e293b;#f59e0b;#1e293b;#f59e0b;#1e293b;#1e293b"
    keyTimes="0; 0.30; 0.35; 0.40; 0.45; 0.50; 0.55; 0.60; 0.65; 0.70; 1"
    dur="4s" repeatCount="indefinite"/>
  <animate xlink:href="#led-left" attributeName="r"
    values="3.5;3.5;5;5;5;5;3.5"
    keyTimes="0; 0.28; 0.32; 0.65; 0.88; 0.92; 1"
    dur="4s" repeatCount="indefinite"/>
  <animate xlink:href="#led-right" attributeName="r"
    values="3.5;3.5;4.5;3.5;4.5;3.5;4.5;3.5;4.5;3.5;3.5"
    keyTimes="0; 0.30; 0.35; 0.40; 0.45; 0.50; 0.55; 0.60; 0.65; 0.70; 1"
    dur="4s" repeatCount="indefinite"/>
</svg>
</div>
<div class="status" id="status">WAITING FOR ROBOT</div>
<div class="message" id="msg">Trying to connect&hellip;</div>

<div class="bar-wrap" id="bar-wrap" style="display:none">
  <div class="bar" id="bar"></div>
</div>

<div class="speed" id="speed"></div>
<div class="detail" id="detail"></div>
<div class="phase" id="phase"></div>

<div class="usb-badge disconnected" id="usb-badge">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 18l-2-2V8l2-2"/><path d="M18 18l2-2V8l-2-2"/><line x1="12" y1="2" x2="12" y2="8"/><circle cx="12" cy="12" r="2"/><line x1="12" y1="14" x2="12" y2="22"/><line x1="7" y1="5" x2="12" y2="8"/><line x1="17" y1="5" x2="12" y2="8"/></svg>
  <span id="usb-text">USB not detected</span>
  <div class="usb-bar-wrap" id="usb-bar-wrap" style="display:none"><div class="usb-bar" id="usb-bar"></div></div>
</div>

<script>
function fmtB(b){
  if(b<1024)return b+' B';
  if(b<1048576)return(b/1024).toFixed(1)+' KB';
  if(b<1073741824)return(b/1048576).toFixed(1)+' MB';
  return(b/1073741824).toFixed(2)+' GB';
}
function fmtS(s){
  if(s<1024)return s.toFixed(0)+' B/s';
  if(s<1048576)return(s/1024).toFixed(1)+' KB/s';
  if(s<1073741824)return(s/1048576).toFixed(1)+' MB/s';
  return(s/1073741824).toFixed(2)+' GB/s';
}

const _ethSvg=document.getElementById('icon').innerHTML;
let _prevState='';

async function poll(){
 try{
  const d=await(await fetch('/api/status')).json();
  const B=document.body,
        icon=document.getElementById('icon'),
        st=document.getElementById('status'),
        msg=document.getElementById('msg'),
        bw=document.getElementById('bar-wrap'),
        bar=document.getElementById('bar'),
        sp=document.getElementById('speed'),
        det=document.getElementById('detail'),
        ph=document.getElementById('phase');
  const changed=d.state!==_prevState;
  _prevState=d.state;

  B.className=d.state;

  if(d.state==='waiting'){
    if(changed)icon.innerHTML=_ethSvg;
    st.textContent='WAITING FOR ROBOT';
    msg.textContent=d.message;
    bw.style.display='none';sp.textContent='';det.textContent='';ph.textContent='';
  }
  else if(d.state==='transferring'){
    if(changed)icon.innerHTML='<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="46" fill="#fff" stroke="rgba(255,255,255,.3)" stroke-width="4"/><rect x="30" y="44" width="40" height="12" rx="2" fill="#dc2626"/></svg>';
    st.textContent='DO NOT UNPLUG';
    msg.textContent=d.message;
    if(d.file_bytes_total>0){
      bw.style.display='block';
      bar.style.width=(d.file_bytes_done/d.file_bytes_total*100).toFixed(1)+'%';
      sp.textContent=fmtS(d.speed_bps);
      det.textContent=fmtB(d.file_bytes_done)+' / '+fmtB(d.file_bytes_total);
      ph.textContent=(d.phase==='downloading'?'\u2B07\uFE0F Downloading from robot':'\u2B06\uFE0F Uploading to cloud')
        +' \u2014 File '+(d.files_completed+1)+' of '+d.files_total;
    }else{bw.style.display='none';sp.textContent='';det.textContent='';ph.textContent='';}
  }
  else if(d.state==='complete'){
    if(changed)icon.innerHTML='<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="46" fill="#fff" stroke="rgba(255,255,255,.3)" stroke-width="4"/><path d="M28 52 L44 68 L72 34" fill="none" stroke="#16a34a" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    st.textContent='SAFE TO UNPLUG';
    msg.textContent=d.message;
    bw.style.display='none';sp.textContent='';
    det.textContent=d.files_total>0?d.files_completed+' of '+d.files_total+' file(s) transferred':'';
    ph.textContent='';
  }
  else if(d.state==='error'){
    if(changed)icon.innerHTML='<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><polygon points="50,8 96,88 4,88" fill="#fff" stroke="rgba(255,255,255,.3)" stroke-width="3" stroke-linejoin="round"/><text x="50" y="76" text-anchor="middle" font-size="52" font-weight="bold" fill="#d97706">!</text></svg>';
    st.textContent='ERROR';
    msg.textContent=d.error||d.message;
    bw.style.display='none';sp.textContent='';det.textContent='';ph.textContent='';
  }

  // ── USB badge ──
  const ub=document.getElementById('usb-badge'),
        ut=document.getElementById('usb-text'),
        ubw=document.getElementById('usb-bar-wrap'),
        ubar=document.getElementById('usb-bar');
  const us=d.usb_status||'disconnected';
  ub.className='usb-badge '+us;
  if(us==='disconnected'){
    ut.textContent='USB not detected';
    ubw.style.display='none';
  }else if(us==='mounted'){
    ut.textContent='USB ready';
    ubw.style.display='none';
  }else if(us==='writing'){
    const pct=d.usb_bytes_total>0?(d.usb_bytes_done/d.usb_bytes_total*100).toFixed(0):'0';
    ut.textContent='USB '+d.usb_files_done+'/'+d.usb_files_total+' ('+pct+'%)';
    ubw.style.display='block';
    ubar.style.width=pct+'%';
  }else if(us==='done'){
    ut.textContent='USB \u2714 Safe to remove';
    ubw.style.display='none';
  }
 }catch(e){}
}

setInterval(poll,500);
poll();
</script>
</body>
</html>
"""

# ── Entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_pending_retry_worker, daemon=True).start()
    threading.Thread(target=_usb_worker, daemon=True).start()
    log.info("Web UI at http://localhost:%d", WEB_PORT)
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)
