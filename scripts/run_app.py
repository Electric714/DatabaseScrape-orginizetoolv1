"""Local launcher. Does not require environment activation."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_environment():
    import fastapi
    import openpyxl
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            raise RuntimeError("Chromium is missing. Run Repair Setup.")
        browser = playwright.chromium.launch()
        browser.close()
    print("Python, application packages and Chromium are ready.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.check:
        check_environment()
        return 0
    import uvicorn
    from app import database as db
    workspace = hashlib.sha256(str(db.DB_PATH.resolve()).encode()).hexdigest()[:16]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    sock = None
    for port in range(args.port, args.port + 10):
        candidate = socket.socket()
        try:
            candidate.bind(("127.0.0.1", port))
            sock = candidate
            break
        except OSError:
            candidate.close()
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                    health = json.load(response)
                if health.get("workspace") == workspace and health.get("application") == "public-data-monitor":
                    print(f"Your workspace is already running: http://127.0.0.1:{port}")
                    if not args.no_browser:
                        webbrowser.open(f"http://127.0.0.1:{port}")
                    return 0
            except Exception:
                pass
    if sock is None:
        raise RuntimeError("No local port is available. Close other copies and try again.")
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", log_level="info", access_log=False))
    def open_when_ready():
        for _ in range(300):
            if server.started:
                print(f"\nOpen {address}\nKeep this window open. Press Ctrl+C here to stop safely.\n")
                if not args.no_browser:
                    webbrowser.open(address)
                return
            if server.should_exit:
                return
            time.sleep(0.1)
    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server.run(sockets=[sock])
        return 0 if server.started else 1
    finally:
        sock.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nWorkspace stopped. You can close this window.")
        raise SystemExit(0)
    except Exception as exc:
        print(f"\nStartup failed: {exc}\nTry the Repair Setup launcher or share logs/setup.log.", file=sys.stderr)
        raise SystemExit(1)
