"""
Converts .hoot files from input-logs/ into .wpilog files in archive/logs/
using the owlet CLI tool. Supports batch conversion and signal selection.
"""

import os
import subprocess
import sys
import shutil
import glob


OWLET_BIN = os.path.join(os.path.dirname(__file__), "owlet-26.1.0-linuxx86-64")
INPUT_DIR = os.path.join(os.path.dirname(__file__), "input-logs")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "archive", "logs")

# Extra signal groups to export via --signals (hex IDs from owlet --scan -F).
# If empty, owlet uses its default signal set.
# To include DeviceTemp, ProcessorTemp, ControlMode, TorqueCurrent, etc.,
# run:  ./owlet-26.1.0-linuxx86-64 <file> /dev/null -S -F
# and add the hex IDs here.
EXTRA_SIGNAL_IDS: list[str] = []


def find_hoot_dirs(input_dir: str) -> list[str]:
    """Return sorted list of timestamp directories containing .hoot files."""
    dirs = []
    for entry in sorted(os.listdir(input_dir)):
        full = os.path.join(input_dir, entry)
        if os.path.isdir(full):
            hoots = glob.glob(os.path.join(full, "*.hoot"))
            if hoots:
                dirs.append(full)
    return dirs


def already_converted(hoot_dir: str, output_dir: str) -> bool:
    """Check if all hoot files in a directory have already been converted."""
    dirname = os.path.basename(hoot_dir)
    for hoot in glob.glob(os.path.join(hoot_dir, "*.hoot")):
        base = os.path.splitext(os.path.basename(hoot))[0]
        wpilog_name = f"{base}.wpilog"
        if not os.path.exists(os.path.join(output_dir, wpilog_name)):
            return False
    return True


def convert_hoot(hoot_path: str, output_dir: str) -> str | None:
    """Convert a single .hoot file to .wpilog using owlet. Returns output path or None on failure."""
    base = os.path.splitext(os.path.basename(hoot_path))[0]
    out_path = os.path.join(output_dir, f"{base}.wpilog")

    if os.path.exists(out_path):
        print(f"  Already exists: {out_path}")
        return out_path

    cmd = [OWLET_BIN, hoot_path, out_path, "-f", "wpilog", "--unlicensed"]
    if EXTRA_SIGNAL_IDS:
        cmd.extend(["-s", ",".join(EXTRA_SIGNAL_IDS)])

    print(f"  Converting: {os.path.basename(hoot_path)} -> {os.path.basename(out_path)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: owlet failed for {hoot_path}")
        print(f"  stderr: {result.stderr.strip()}")
        return None

    return out_path


def convert_all(input_dir: str = INPUT_DIR, output_dir: str = OUTPUT_DIR):
    """Convert all .hoot files in input-logs/ subdirectories to .wpilog."""
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.isfile(OWLET_BIN):
        print(f"ERROR: owlet binary not found at {OWLET_BIN}")
        sys.exit(1)

    dirs = find_hoot_dirs(input_dir)
    print(f"Found {len(dirs)} log directories in {input_dir}")

    converted = 0
    skipped = 0
    failed = 0

    for hoot_dir in dirs:
        dirname = os.path.basename(hoot_dir)

        if already_converted(hoot_dir, output_dir):
            skipped += 1
            continue

        print(f"Processing {dirname}...")
        for hoot in sorted(glob.glob(os.path.join(hoot_dir, "*.hoot"))):
            result = convert_hoot(hoot, output_dir)
            if result:
                converted += 1
            else:
                failed += 1

    print(f"\nDone: {converted} converted, {skipped} dirs skipped (already done), {failed} failed")


if __name__ == "__main__":
    convert_all()
