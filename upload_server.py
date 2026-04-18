#!/usr/bin/env python3
"""
Robot Telemetry — Log Upload Server
FRC Team 4607 "Power Play" — 2026 season (second robot)

Lightweight HTTP server that accepts .hoot file uploads and drops them
into the input-logs/ directory for the telemetry pipeline to pick up
automatically.  Validates that uploaded files are genuine CTR Electronics
hoot logs before accepting them.

Endpoints:
    GET  /             — Simple upload form (for browser use from the pit)
    POST /api/upload   — Multipart file upload endpoint
    GET  /api/health   — Health-check

Usage:
    python upload_server.py                     # default port 8080
    python upload_server.py --port 9000         # custom port
    python upload_server.py --host 0.0.0.0      # listen on all interfaces (default)

The existing run.py watchdog will detect new files in input-logs/ and
process them automatically.
"""

import argparse
import html
import http.server
import io
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, "input-logs")
OWLET_BIN = os.path.join(BASE_DIR, "owlet-26.1.0-linuxx86-64")

ALLOWED_EXTENSIONS = {".hoot"}
MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB
MIN_UPLOAD_SIZE = 1024  # 1 KB — real hoot files are many KB/MB

# CTR hoot filename: alphanumeric prefix + underscore-separated date/time
# Examples:
#   247B94A646324B532020204A0F2C12FF_2026-03-05_00-33-23.hoot  (device serial)
#   NDGF_E6_rio_2026-03-14_19-24-56.hoot                       (event name)
HOOT_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9_]+_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.hoot$"
)

# ── Hoot binary header constants ──────────────────────────────────────────────
# The .hoot format starts with a 64-byte bus name field (printable ASCII,
# null-padded), followed by padding and a uint16 LE version at offset 0x46.
_HOOT_HEADER_SIZE = 0x48          # minimum bytes needed for header check
_HOOT_BUS_NAME_LEN = 64          # fixed-width bus name field
_HOOT_VERSION_OFFSET = 0x46      # uint16 LE version number

# Known dangerous file signatures (magic bytes) that must never be accepted
_DANGEROUS_SIGNATURES = [
    b"\x7fELF",          # ELF executable
    b"MZ",               # PE/DOS executable
    b"PK",               # ZIP / Office / JAR
    b"\x1f\x8b",         # gzip
    b"BZh",              # bzip2
    b"\xfd7zXZ\x00",     # xz
    b"Rar!\x1a\x07",     # RAR
    b"WPILOG",           # wpilog (not accepted — only .hoot)
    b"<!DOCTYPE",        # HTML
    b"<!doctype",
    b"<html",
    b"<HTML",
    b"<?xml",            # XML
    b"<?php",            # PHP
    b"%PDF",             # PDF
    b"\xca\xfe\xba\xbe", # Mach-O / Java class
    b"\xfe\xed\xfa",     # Mach-O
]
# Text-based script shebangs
_SCRIPT_PREFIXES = [b"#!", b"#!/"]

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("upload-server")

# ── HTML upload form ───────────────────────────────────────────────────────────
UPLOAD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robot Telemetry — Upload Logs</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #1a1a2e; color: #e0e0e0; min-height: 100vh;
         display: flex; flex-direction: column; align-items: center; padding: 2rem; }
  h1 { color: #ffd700; margin-bottom: 0.5rem; }
  .subtitle { color: #888; margin-bottom: 2rem; font-size: 0.9rem; }
  .upload-area { background: #16213e; border: 2px dashed #444;
                 border-radius: 12px; padding: 3rem 2rem; text-align: center;
                 max-width: 600px; width: 100%; cursor: pointer;
                 transition: border-color 0.2s, background 0.2s; }
  .upload-area.dragover { border-color: #ffd700; background: #1a2744; }
  .upload-area p { margin-bottom: 1rem; }
  .upload-area .icon { font-size: 3rem; margin-bottom: 1rem; }
  input[type="file"] { display: none; }
  .btn { background: #ffd700; color: #1a1a2e; border: none; padding: 0.75rem 2rem;
         border-radius: 6px; font-size: 1rem; font-weight: 600; cursor: pointer;
         transition: background 0.2s; }
  .btn:hover { background: #ffed4a; }
  .btn:disabled { background: #555; color: #999; cursor: not-allowed; }
  #file-list { margin-top: 1rem; text-align: left; font-size: 0.85rem; color: #aaa; }
  #status { margin-top: 1.5rem; max-width: 600px; width: 100%; }
  .status-msg { padding: 0.75rem 1rem; border-radius: 6px; margin-bottom: 0.5rem;
                font-size: 0.9rem; }
  .status-ok { background: #1b4332; color: #95d5b2; }
  .status-err { background: #4a1c1c; color: #f5a5a5; }
  .status-info { background: #1a2744; color: #90caf9; }
  .progress { width: 100%; height: 6px; background: #333; border-radius: 3px;
              margin-top: 0.5rem; overflow: hidden; display: none; }
  .progress-bar { height: 100%; background: #ffd700; width: 0%;
                  transition: width 0.3s; }
</style>
</head>
<body>
<h1>Robot Telemetry</h1>
<p class="subtitle">FRC 4607 — Drop log files here to upload</p>

<div class="upload-area" id="drop-zone">
  <div class="icon">&#128229;</div>
  <p>Drag &amp; drop <strong>.hoot</strong> log files here</p>
  <p>— or —</p>
  <button class="btn" onclick="document.getElementById('file-input').click()">Browse Files</button>
  <input type="file" id="file-input" multiple accept=".hoot">
  <div id="file-list"></div>
  <div class="progress" id="progress"><div class="progress-bar" id="progress-bar"></div></div>
</div>
<div id="status"></div>

<script>
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const fileList = document.getElementById('file-list');
const statusDiv = document.getElementById('status');
const progress = document.getElementById('progress');
const progressBar = document.getElementById('progress-bar');

function addStatus(msg, cls) {
  const d = document.createElement('div');
  d.className = 'status-msg ' + cls;
  d.textContent = msg;
  statusDiv.prepend(d);
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + ' KB';
  return (bytes/(1024*1024)).toFixed(1) + ' MB';
}

async function uploadFiles(files) {
  if (!files.length) return;
  const validFiles = Array.from(files).filter(f => f.name.endsWith('.hoot'));
  if (!validFiles.length) { addStatus('No .hoot files selected.', 'status-err'); return; }

  for (const file of validFiles) {
    addStatus('Uploading ' + file.name + ' (' + formatSize(file.size) + ')...', 'status-info');
    progress.style.display = 'block';
    progressBar.style.width = '0%';

    const formData = new FormData();
    formData.append('file', file);

    try {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/upload');
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) progressBar.style.width = (e.loaded/e.total*100) + '%';
      };
      await new Promise((resolve, reject) => {
        xhr.onload = () => {
          progress.style.display = 'none';
          if (xhr.status === 200) {
            const r = JSON.parse(xhr.responseText);
            addStatus('Uploaded ' + r.filename + ' successfully.', 'status-ok');
          } else {
            const r = JSON.parse(xhr.responseText);
            addStatus('Failed: ' + (r.error || xhr.statusText), 'status-err');
          }
          resolve();
        };
        xhr.onerror = () => { progress.style.display='none'; addStatus('Network error uploading ' + file.name, 'status-err'); reject(); };
        xhr.send(formData);
      });
    } catch(e) { /* handled above */ }
  }
  fileList.textContent = '';
  fileInput.value = '';
}

function showFileList(files) {
  const names = Array.from(files).map(f => f.name + ' (' + formatSize(f.size) + ')');
  fileList.textContent = names.length ? 'Selected: ' + names.join(', ') : '';
}

dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => { e.preventDefault(); dropZone.classList.remove('dragover'); uploadFiles(e.dataTransfer.files); });
fileInput.addEventListener('change', () => { showFileList(fileInput.files); uploadFiles(fileInput.files); });
</script>
</body>
</html>
"""


# ── Validation helpers ─────────────────────────────────────────────────────────

def _has_dangerous_signature(data: bytes) -> bool:
    """Return True if *data* starts with a known non-hoot magic sequence."""
    for sig in _DANGEROUS_SIGNATURES:
        if data[:len(sig)] == sig:
            return True
    for prefix in _SCRIPT_PREFIXES:
        if data[:len(prefix)] == prefix:
            return True
    return False


def _validate_hoot_header(data: bytes) -> str | None:
    """Check the .hoot binary header structure.

    Returns None if the header looks valid, or an error string describing
    the problem.

    Hoot header layout (observed from real CTR Electronics files):
      0x00-0x3F  64-byte bus name — printable ASCII, null-padded
      0x40-0x45  padding (zeros)
      0x46-0x47  version number (uint16 LE)
    """
    if len(data) < _HOOT_HEADER_SIZE:
        return "file too small for a valid hoot header"

    # ── Bus name field (first 64 bytes) ───────────────────────────────
    bus_field = data[:_HOOT_BUS_NAME_LEN]

    # Find end of the name string (first null byte)
    try:
        null_pos = bus_field.index(0)
    except ValueError:
        null_pos = _HOOT_BUS_NAME_LEN

    if null_pos == 0:
        return "hoot header bus name is empty"

    name_bytes = bus_field[:null_pos]
    pad_bytes = bus_field[null_pos:]

    # Name must be printable ASCII
    if not all(32 <= b < 127 for b in name_bytes):
        return "hoot header bus name contains non-ASCII characters"

    # Padding after name must be all zeros
    if not all(b == 0 for b in pad_bytes):
        return "hoot header bus name padding is corrupt"

    # ── Version field ─────────────────────────────────────────────────
    version = int.from_bytes(
        data[_HOOT_VERSION_OFFSET : _HOOT_VERSION_OFFSET + 2],
        byteorder="little",
    )
    if version == 0:
        return "hoot header version is zero"

    return None


def _validate_hoot_with_owlet(path: str) -> str | None:
    """Run owlet --scan on *path*.  Returns None on success or an error string."""
    if not os.path.isfile(OWLET_BIN):
        # If owlet isn't available, skip this check (non-fatal)
        log.warning("owlet binary not found at %s — skipping content validation", OWLET_BIN)
        return None
    try:
        result = subprocess.run(
            [OWLET_BIN, path, "/dev/null", "--scan"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()[:200]
            return f"owlet rejected file: {stderr}" if stderr else "owlet rejected file"
    except subprocess.TimeoutExpired:
        return "owlet validation timed out"
    except Exception as exc:
        log.warning("owlet validation error: %s", exc)
        # Non-fatal — allow the upload through
    return None


def _sanitize_filename(raw: str) -> str | None:
    """Return a safe basename or None if the name is invalid.

    Rejects null bytes, control characters, path traversal, and hidden files.
    """
    if not raw:
        return None
    # Strip to basename (no directory components)
    name = os.path.basename(raw)
    if not name or name.startswith("."):
        return None
    # Reject null bytes and control characters
    if "\x00" in name or any(ord(c) < 32 for c in name):
        return None
    # Path traversal / suspicious patterns
    if ".." in name or "/" in name or "\\" in name:
        return None
    return name


class UploadHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for log file uploads."""

    def log_message(self, fmt, *args):
        log.info(fmt, *args)

    def _send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status, content):
        body = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send_html(200, UPLOAD_HTML)
        elif self.path == "/api/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/upload":
            self._send_json(404, {"error": "not found"})
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json(400, {"error": "expected multipart/form-data"})
            return

        # Check Content-Length before reading
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_UPLOAD_SIZE:
            self._send_json(413, {"error": f"file too large (max {MAX_UPLOAD_SIZE // (1024*1024)} MB)"})
            return
        if content_length == 0:
            self._send_json(400, {"error": "empty request"})
            return

        # Extract boundary from Content-Type
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):]
                break
        if not boundary:
            self._send_json(400, {"error": "missing multipart boundary"})
            return

        # Read the entire body
        body = self.rfile.read(content_length)

        # Parse multipart manually (avoids deprecated cgi module)
        boundary_bytes = boundary.encode()
        delimiter = b"--" + boundary_bytes
        parts = body.split(delimiter)

        file_data = None
        file_name = None

        for part in parts:
            if not part or part == b"--\r\n" or part == b"--":
                continue
            # Split headers from body at first double CRLF
            if b"\r\n\r\n" not in part:
                continue
            raw_headers, raw_body = part.split(b"\r\n\r\n", 1)
            # Strip trailing \r\n from body
            if raw_body.endswith(b"\r\n"):
                raw_body = raw_body[:-2]

            headers_str = raw_headers.decode("utf-8", errors="replace")
            # Check if this part has a filename (i.e., it's a file upload)
            if 'filename="' in headers_str:
                # Extract filename
                for header_line in headers_str.split("\r\n"):
                    if "filename=" in header_line:
                        # Find filename="..."
                        idx = header_line.index('filename="') + len('filename="')
                        end = header_line.index('"', idx)
                        file_name = header_line[idx:end]
                        break
                file_data = raw_body
                break

        if file_data is None or not file_name:
            self._send_json(400, {"error": "no file provided (use field name 'file')"})
            return

        # ── 1. Filename sanitization ──────────────────────────────────────
        raw_name = _sanitize_filename(file_name)
        if not raw_name:
            self._send_json(400, {"error": "invalid filename"})
            return

        _, ext = os.path.splitext(raw_name)
        if ext.lower() not in ALLOWED_EXTENSIONS:
            self._send_json(400, {
                "error": f"unsupported file type '{html.escape(ext)}' (only .hoot files are accepted)"
            })
            return

        # ── 2. Filename pattern check ─────────────────────────────────────
        if not HOOT_FILENAME_RE.match(raw_name):
            self._send_json(400, {
                "error": (
                    "filename does not match expected hoot pattern "
                    "(expected: <name>_<YYYY-MM-DD>_<HH-MM-SS>.hoot)"
                )
            })
            return

        # ── 3. Size checks ────────────────────────────────────────────────
        if len(file_data) < MIN_UPLOAD_SIZE:
            self._send_json(400, {"error": "file too small to be a valid hoot log"})
            return

        # ── 4. Reject known dangerous file signatures ─────────────────────
        if _has_dangerous_signature(file_data):
            log.warning("Rejected upload %s — dangerous file signature detected", raw_name)
            self._send_json(400, {"error": "file content does not look like a hoot log"})
            return

        # ── 5. Validate hoot binary header structure ──────────────────────
        header_err = _validate_hoot_header(file_data)
        if header_err:
            log.warning("Rejected upload %s — %s", raw_name, header_err)
            self._send_json(400, {"error": "file content does not look like a hoot log"})
            return

        # ── 6. Write to temp file, then validate with owlet ───────────────
        # Place in a subdirectory (the pipeline expects hoot files in dirs)
        dir_name = os.path.splitext(raw_name)[0]
        dest_dir = os.path.join(INPUT_DIR, dir_name)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, raw_name)

        try:
            fd, tmp_path = tempfile.mkstemp(dir=dest_dir, suffix=".hoot.tmp")
            try:
                with os.fdopen(fd, "wb") as tmp_f:
                    tmp_f.write(file_data)

                # ── 7. Owlet content validation ───────────────────────────
                validation_err = _validate_hoot_with_owlet(tmp_path)
                if validation_err:
                    log.warning("Rejected upload %s — %s", raw_name, validation_err)
                    os.unlink(tmp_path)
                    self._send_json(400, {"error": "file content is not a valid hoot log"})
                    return

                os.rename(tmp_path, dest_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            log.error("Failed to save upload %s: %s", raw_name, e)
            self._send_json(500, {"error": "failed to save file"})
            return

        file_size = len(file_data)
        log.info("Accepted upload: %s (%d bytes)", raw_name, file_size)
        self._send_json(200, {
            "filename": raw_name,
            "size": file_size,
            "message": "file uploaded successfully — it will be processed automatically",
        })


class ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    """Allow socket reuse for fast restarts."""
    allow_reuse_address = True
    allow_reuse_port = True


def main():
    parser = argparse.ArgumentParser(description="Robot Telemetry — Log Upload Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    args = parser.parse_args()

    os.makedirs(INPUT_DIR, exist_ok=True)

    server = ThreadedHTTPServer((args.host, args.port), UploadHandler)

    shutdown = threading.Event()

    def on_signal(signum, _frame):
        log.info("Received signal %d, shutting down ...", signum)
        shutdown.set()
        server.shutdown()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log.info("Upload server listening on http://%s:%d", args.host, args.port)
    log.info("Upload page: http://localhost:%d", args.port)
    log.info("API endpoint: POST http://localhost:%d/api/upload", args.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log.info("Upload server stopped.")


if __name__ == "__main__":
    main()
