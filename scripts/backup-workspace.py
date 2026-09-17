import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import backup

parser = argparse.ArgumentParser(description="Create a crash-consistent Paralegal Research Desk backup")
parser.add_argument("output", nargs="?", help="ZIP path; defaults to data/backups/<timestamp>.zip")
args = parser.parse_args()
output = Path(args.output) if args.output else Path("data/backups") / f"paralegal-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
manifest = backup.create_backup_file(output)
print(f"Backup created: {output}")
print(f"Schema version: {manifest['schema_version']}; credentials included: {manifest['credentials_included']}")
