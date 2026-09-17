import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import backup

parser = argparse.ArgumentParser(description="Verify and restore a Paralegal Research Desk backup")
parser.add_argument("archive", help="Backup ZIP created by backup-workspace.py or the maintenance API")
parser.add_argument("--target", help="Destination SQLite path; defaults to the configured workspace database")
parser.add_argument("--overwrite", action="store_true", help="Replace an existing database. Stop the application first.")
args = parser.parse_args()
result = backup.restore_backup(Path(args.archive), args.target, overwrite=args.overwrite)
print(f"Restore complete: {result['restored_to']}")
print(f"Validated schema version: {result['validated_schema_version']}")
