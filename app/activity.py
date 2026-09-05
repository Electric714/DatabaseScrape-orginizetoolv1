"""Structured, persistent application activity. Never logs scraped record bodies."""
import io
import json
import logging
import platform
import re
import sys
import traceback
import zipfile
from urllib.parse import urlsplit, urlunsplit

from . import database as db

APP_VERSION = "0.3.0"
URL = re.compile(r"https?://[^\s<>\"']+")
SECRET = re.compile(r"(?i)(authorization|cookie|password|token|api[_-]?key|secret)(\s*[:=]\s*)([^\s,;]+)")


def redact(value):
    def safe_url(match):
        try:
            url = urlsplit(match[0])
            return urlunsplit((url.scheme, url.hostname or "", url.path, "", ""))
        except ValueError:
            return "[invalid URL]"
    text = URL.sub(safe_url, str(value))
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:Bearer|Basic)\s+\S+", r"\1[redacted]", text)
    text = SECRET.sub(r"\1\2[redacted]", text)
    text = re.sub(r"(?i)[A-Z]:\\Users\\[^\\\s]+", r"C:\\Users\\[user]", text)
    return text[:12000]


class ActivityHandler(logging.Handler):
    def emit(self, record):
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + "".join(traceback.format_exception(*record.exc_info))
        details = getattr(record, "details", {})
        clean = {str(k): "[redacted]" if re.search(r"(?i)token|secret|password|cookie|authorization|api[_-]?key", str(k)) else redact(v) for k, v in details.items()}
        future = db._EXECUTOR.submit(db._sync_add_event, record.levelname, record.name,
            redact(message), clean, getattr(record, "job_id", None), getattr(record, "source_id", None))
        def completed(result):
            if result.exception():
                # Do not recurse into logging when the disk/database is unavailable.
                sys.stderr.write("Activity could not be saved: " + redact(result.exception()) + "\n")
        future.add_done_callback(completed)


def install():
    for name in ("app", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        if not any(isinstance(h, ActivityHandler) for h in logger.handlers):
            logger.addHandler(ActivityHandler())


def emit(level, message, *, job_id=None, source_id=None, **details):
    install()
    logging.getLogger("app.activity").log(getattr(logging, level), message,
        extra={"details": details, "job_id": job_id, "source_id": source_id})


async def bundle():
    events = await db.list_events(limit=5000)
    jobs = await db.list_jobs(limit=30)
    summary = {
        "application": "Public Data Monitor", "version": APP_VERSION,
        "created_at": db.utcnow(), "python": platform.python_version(),
        "platform": platform.system(), "counts": await db.stats(),
        "recent_jobs": [{k: v for k, v in job.items() if k not in {"source_name", "message"}} for job in jobs],
        "event_count": len(events["items"]),
        "notes": "No collected records, environment variables, cookies or database file are included. Site paths and error messages may contain sensitive context; review before sharing.",
    }
    def create_zip():
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("summary.json", json.dumps(summary, indent=2))
            archive.writestr("activity.json", json.dumps(events["items"], indent=2, ensure_ascii=False))
            archive.writestr("activity.txt", "\n".join(
                f'{e["created_at"]} [{e["level"]}] {e["message"]}\n  {json.dumps(e["details"], ensure_ascii=False)}'
                for e in events["items"]))
            archive.writestr("READ-ME.txt", "Share this ZIP when requesting help. Review its contents first.\nIt contains retained activity (up to 5,000 events) and system/crawl counters, not scraped records.\n")
        return output.getvalue()
    import asyncio
    return await asyncio.to_thread(create_zip)
