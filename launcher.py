"""Starts the PFA tax tracker, as a desktop window or in the browser.

    python3 launcher.py               the desktop window
    python3 launcher.py --web         the browser version at http://127.0.0.1:8765
    python3 launcher.py --web --port 8790

The built executable (PFA-Tax-Tracker, PFA-Tax-Tracker.exe) takes the same options. Both ways
use the same files: settings.json, rates.csv, caen_codes.xlsx and data/ in the home folder
(see tracker/config.py).
"""
import argparse
import ctypes
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
APP_TITLE = "PFA tax tracker"

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="PFA-Tax-Tracker",
        description="PFA tax tracker. Without options it opens the desktop window; "
                    "with --web it runs the browser version. Both use the same data and settings.")
    parser.add_argument("--web", action="store_true",
                        help=f"run the browser version at http://{HOST}:PORT and open it in the browser")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, metavar="N",
                        help=f"port for --web (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="with --web: start the server without opening the browser")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.port != DEFAULT_PORT and not args.web:
        parser.error("--port is only used with --web")
    return args


def fix_missing_streams() -> None:
    """A windowed .exe has no console: send output to data/server.log instead of nowhere
    (uvicorn's logging fails when sys.stdout / sys.stderr are None)."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from tracker.config import LOG_PATH
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


# --- browser mode -------------------------------------------------------------------------------

def port_in_use(port: int, host: str = HOST) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def tracker_running(port: int, host: str = HOST) -> bool:
    """True if the PFA tax tracker (not some other program) answers on the port."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=3) as response:
            return APP_TITLE in response.read(200_000).decode("utf-8", "replace")
    except Exception:
        return False


def open_browser_when_ready(url: str, port: int, timeout: float = 30) -> None:
    def wait_and_open():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_in_use(port):
                webbrowser.open(url)
                return
            time.sleep(0.2)
    threading.Thread(target=wait_and_open, daemon=True).start()


def run_web(port: int, open_browser: bool = True) -> int:
    url = f"http://{HOST}:{port}/"
    if port_in_use(port):
        if tracker_running(port):
            print(f"{APP_TITLE} is already running at {url}")
            if open_browser:
                webbrowser.open(url)
            return 0
        print(f"Port {port} is used by another program. Choose another one with --port.", file=sys.stderr)
        return 1
    import uvicorn
    from tracker.web import app
    print(f"{APP_TITLE} is starting at {url} (stop it with Ctrl+C)")
    if open_browser:
        open_browser_when_ready(url, port)
    uvicorn.run(app, host=HOST, port=port)
    return 0


# --- desktop mode -------------------------------------------------------------------------------

def support_dirs() -> list[Path]:
    """Where the bundled libxcb-cursor stand-in may be: next to the program files."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return [base / "desktop_support", base]


def load_xcb_cursor() -> str | None:
    """Qt 6 on Linux needs libxcb-cursor.so.0. If the system lacks it, load the stand-in from
    desktop_support/ first; the dynamic linker then reuses it when Qt asks for it.
    Returns the path that was loaded, "system", or None (not Linux / nothing found)."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        ctypes.CDLL("libxcb-cursor.so.0", mode=ctypes.RTLD_GLOBAL)
        return "system"
    except OSError:
        pass
    for folder in support_dirs():
        stub = folder / "libxcb-cursor.so.0"
        if stub.is_file():
            try:
                ctypes.CDLL(str(stub), mode=ctypes.RTLD_GLOBAL)
                return str(stub)
            except OSError:
                continue
    return None


def run_desktop() -> int:
    load_xcb_cursor()
    try:
        from tracker.desktop.app import main as desktop_main
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("tracker.desktop"):
            raise
        print(f"The desktop window could not be started ({exc}). "
              "The browser version still works: run with --web.", file=sys.stderr)
        return 1
    result = desktop_main()
    return result if isinstance(result, int) else 0


def main(argv=None) -> int:
    fix_missing_streams()
    args = parse_args(argv)
    if args.web:
        return run_web(args.port, open_browser=not args.no_browser)
    return run_desktop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
