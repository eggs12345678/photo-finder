"""Desktop shell for the installed app.

Starts the local API in the background, opens the browser, and keeps a tray
icon so the server can be stopped again. ``photofinder stop`` does the same
from a shortcut or terminal.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Photo Finder"


def default_port() -> int:
    try:
        return int(os.environ.get("PHOTOFINDER_PORT", "8642"))
    except ValueError:
        return 8642


def data_dir() -> Path:
    configured = os.environ.get("PHOTOFINDER_DATA")
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "PhotoFinder" / "data"


def app_root() -> Path:
    """Folder that contains ``photofinder/`` and ``web/``."""
    return Path(__file__).resolve().parents[1]


def url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _open_browser(port: int) -> None:
    if os.environ.get("PHOTOFINDER_NO_BROWSER") in {"1", "true", "yes"}:
        return
    try:
        webbrowser.open(url(port))
    except Exception:
        pass


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def api_awake(port: int) -> bool:
    try:
        with urllib.request.urlopen(url(port) + "api/health", timeout=0.8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return isinstance(payload, dict) and "boot" in payload and "data_dir" in payload
    except Exception:
        return False


def _pid_file() -> Path:
    return data_dir() / "server.pid"


def _read_pid() -> int | None:
    try:
        value = _pid_file().read_text(encoding="utf-8").strip()
        return int(value)
    except (OSError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    try:
        data_dir().mkdir(parents=True, exist_ok=True)
        _pid_file().write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _clear_pid(pid: int | None = None) -> None:
    try:
        if pid is None or _read_pid() == pid:
            _pid_file().unlink(missing_ok=True)
    except OSError:
        pass


def _creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )


def _spawn_server(port: int) -> subprocess.Popen:
    root = app_root()
    env = os.environ.copy()
    env["PHOTOFINDER_DATA"] = str(data_dir())
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    log_path = data_dir() / "server.log"
    try:
        data_dir().mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size > 5_000_000:
            log_path.unlink()
    except OSError:
        pass
    log = open(log_path, "ab", buffering=0)
    try:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "photofinder",
                "serve",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=_creation_flags(),
        )
    finally:
        log.close()


def _wait_for_api(process: subprocess.Popen, port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        if api_awake(port):
            return True
        time.sleep(0.25)
    return api_awake(port)


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=8)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _say(message: str) -> None:
    stream = sys.stderr or sys.stdout
    try:
        if stream is not None:
            print(message, file=stream, flush=True)
    except Exception:
        pass


def _alert(message: str) -> None:
    """Show a message box as well as logging, so pythonw failures are visible."""
    _say(message)
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:
        pass


def _terminate_pid(pid: int) -> bool:
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            probe = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                creationflags=flags,
            )
        except Exception:
            return False
        # Do not kill a recycled PID that is no longer our Python process.
        if "python" not in probe.stdout.lower():
            return False
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=flags,
        )
        return result.returncode == 0
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _icon_image():
    from PIL import Image, ImageDraw

    icon_path = app_root().parent / "assets" / "icon.ico"
    if icon_path.exists():
        try:
            return Image.open(icon_path).convert("RGBA")
        except Exception:
            pass
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=14, fill=(14, 16, 20, 255))
    draw.ellipse((12, 12, 40, 40), outline=(216, 240, 90, 255), width=6)
    draw.line((36, 36, 52, 52), fill=(216, 240, 90, 255), width=6)
    return image


def run_desktop(port: int | None = None) -> int:
    port = port or default_port()
    if api_awake(port):
        _open_browser(port)
        return 0
    if port_open(port):
        _alert(f"Port {port} is already used by another program.")
        return 1

    try:
        process = _spawn_server(port)
    except Exception as exc:
        _alert(f"Could not start the server: {exc}")
        return 1
    _write_pid(process.pid)

    if not _wait_for_api(process, port):
        _alert(f"The server did not start. See {data_dir() / 'server.log'}")
        _terminate(process)
        _clear_pid(process.pid)
        return 1

    _open_browser(port)

    try:
        import pystray
    except Exception:
        # Without a tray there is no way to stop a hidden server; keep this
        # process attached to it instead.
        _say(f"Photo Finder is running at {url(port)}. Press Ctrl+C to stop.")
        try:
            process.wait()
        except KeyboardInterrupt:
            pass
        finally:
            _terminate(process)
            _clear_pid(process.pid)
        return 0

    stopped = threading.Event()

    def quit_app(*_args) -> None:
        stopped.set()
        _terminate(process)
        _clear_pid(process.pid)
        try:
            icon.stop()
        except Exception:
            pass

    def monitor() -> None:
        while not stopped.wait(2.0):
            if process.poll() is not None:
                _clear_pid(process.pid)
                try:
                    icon.stop()
                except Exception:
                    pass
                return

    icon = pystray.Icon(
        "photo-finder",
        _icon_image(),
        APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem("Open Photo Finder", lambda: webbrowser.open(url(port)), default=True),
            pystray.MenuItem("Open data folder", lambda: _open_folder(data_dir())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Photo Finder", quit_app),
        ),
    )
    threading.Thread(target=monitor, daemon=True).start()
    try:
        icon.run()
    finally:
        _terminate(process)
        _clear_pid(process.pid)
    return 0


def _open_folder(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def stop_server() -> int:
    pid = _read_pid()
    if pid is None:
        _say("Photo Finder is not running.")
        return 0
    ok = _terminate_pid(pid)
    _clear_pid(pid)
    _say("Photo Finder stopped." if ok else "Photo Finder was not running.")
    return 0
