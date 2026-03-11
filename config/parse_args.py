import argparse
import os
import pathlib
import sys

parser = argparse.ArgumentParser()

parser.add_argument(
    "-d",
    "--database",
    help="A SQLAlchemy connection string that specifies the database to connect to.",
    required=True,
)

parser.add_argument(
    "-g",
    "--groups",
    help="The directory where the files containing the metrics are stored.",
    default="groups",
)

parser.add_argument(
    "-a",
    "--archive",
    help="A directory to store the logs and metrics. The logs subdirectory of this directory will be scanned for new log files to analyse.",
    default="archive",
)

parser.add_argument(
    "-r",
    "--rescan",
    help="Check all log files in the archive for unrun groups. Useful if you have updated the groups and want to rerun them.",
    action="store_true",
)

args = parser.parse_args()

groupPath = pathlib.PurePath(args.groups)
if not (os.path.exists(groupPath) and os.path.isdir(groupPath)):
    print(
        f"Group directory {args.groups} doesn't exist or isn't a directory.",
        file=sys.stderr,
    )
    sys.exit(1)
for group in os.listdir(args.groups):
    full_path = os.path.join(args.groups, group)
    if os.path.isdir(full_path):
        continue
    split = group.split(".")
    if group != "__pycache__" and (len(split) == 1 or split[1] != "py"):
        print(
            f"File {group} in {args.groups} is not a .py file.",
            file=sys.stderr,
        )
        sys.exit(1)

archivePath = pathlib.PurePath(args.archive)
logsPath = os.path.join(archivePath, "logs")
metricsPath = os.path.join(archivePath, "metrics")
if os.path.exists(logsPath):
    if os.path.isfile(logsPath):
        print(f"Log directory in {args.archive} is a file.", file=sys.stderr)
        sys.exit(1)
else:
    os.mkdir(logsPath)

if os.path.exists(metricsPath):
    if os.path.isfile(metricsPath):
        print(f"Metric directory in {args.archive} is a file.", file=sys.stderr)
        sys.exit(1)
else:
    os.mkdir(metricsPath)
