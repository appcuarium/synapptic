"""The dashboard as a managed process: `synapptic dashboard start|stop|restart|status`.

`start` launches the server detached (its own session, output to ~/.synapptic/dashboard.log), records the
pid in ~/.synapptic/dashboard.pid and waits until /api/health answers. `stop` ends the recorded process,
or the one listening on the port when there is no record (an older dashboard started in a terminal), and
waits until the port is free. `status` says what answers on the port. Nothing here imports fastapi: the
[dashboard] extra is checked by the command before the server is spawned.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from synapptic.config import SYNAPPTIC_DIR

PID_FILE = SYNAPPTIC_DIR / "dashboard.pid"
LOG_FILE = SYNAPPTIC_DIR / "dashboard.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5200
START_TIMEOUT = 15.0      # seconds for the server to answer /api/health after the spawn
STOP_TIMEOUT = 10.0       # seconds for the process to leave the port after SIGTERM
POLL = 0.2
LOG_TAIL = 20             # lines of the log shown when a start fails


class DashboardError(Exception):
    pass


def url_for(host: str, port: int) -> str:
    return f"http://{host}:{port}/"


def health(host: str, port: int) -> dict | None:
    """The server's /api/health answer, or None when nothing answers."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1) as reply:
            return json.loads(reply.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap(pid: int) -> None:
    """Collect the process when it is a child of this one (a terminated child stays visible to `kill -0`
    until its parent waits on it); nothing happens for someone else's process or on Windows."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError, AttributeError):
        pass


def recorded_pid(pid_file: Path = PID_FILE) -> int | None:
    """The pid in the pid file when that process is alive; a stale file is removed."""
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    if pid_alive(pid):
        return pid
    pid_file.unlink(missing_ok=True)
    return None


def listening_pid(port: int) -> int | None:
    """The process listening on the port, through lsof where it exists (a dashboard with no pid file)."""
    lsof = shutil.which("lsof")
    if not lsof:
        return None
    try:
        found = subprocess.run([lsof, "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    pids = [int(line) for line in found.stdout.split() if line.isdigit()]
    return pids[0] if pids else None


def serve_command(host: str, port: int) -> list[str]:
    """The foreground server, run by the interpreter that holds synapptic."""
    return [sys.executable, "-c", "from synapptic.cli import cli; cli()", "dashboard", "serve", "--host", host, "--port", str(port)]


def restart_command(host: str, port: int) -> list[str]:
    """`synapptic dashboard restart` for this server, as the page's restart button spawns it (detached, so it
    outlives the server it stops)."""
    return [sys.executable, "-c", "from synapptic.cli import cli; cli()", "dashboard", "restart", "--host", host, "--port", str(port)]


def spawn_detached(command: list[str], log: Path) -> subprocess.Popen:
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as output:
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
            return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)


def log_tail(log: Path) -> str:
    try:
        return "\n".join(log.read_text(errors="replace").splitlines()[-LOG_TAIL:])
    except OSError:
        return ""


class Service:
    """One dashboard as a managed process: its pid file, its log and the command that serves it in the
    foreground. The default is `synapptic dashboard`; another dashboard built on this one brings its own."""

    def __init__(self, pid_file: Path = PID_FILE, log_file: Path = LOG_FILE, command=serve_command):
        self.pid_file = pid_file
        self.log_file = log_file
        self.command = command

    def recorded_pid(self) -> int | None:
        return recorded_pid(self.pid_file)

    def status(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
        """{"running", "pid", "recorded", "url", "log", "since"}: running means /api/health answers."""
        answer = health(host, port)
        pid = self.recorded_pid()
        recorded = pid is not None
        if pid is None:
            pid = listening_pid(port)
        since = None
        if recorded:
            try:
                since = self.pid_file.stat().st_mtime
            except OSError:
                since = None
        return {"running": answer is not None, "pid": pid, "recorded": recorded, "url": url_for(host, port),
                "log": str(self.log_file), "since": since, "protocol": (answer or {}).get("protocol")}

    def start(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
        """Start the dashboard detached and wait for it to answer. {"started", "pid", "url", "log"}."""
        current = self.status(host, port)
        if current["running"]:
            return {"started": False, "pid": current["pid"], "url": current["url"], "log": current["log"]}
        process = spawn_detached(self.command(host, port), self.log_file)
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(f"{process.pid}\n")
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if health(host, port) is not None:
                return {"started": True, "pid": process.pid, "url": url_for(host, port), "log": str(self.log_file)}
            if process.poll() is not None:
                break
            time.sleep(POLL)
        self.pid_file.unlink(missing_ok=True)
        if process.poll() is None:
            process.terminate()
        raise DashboardError(f"the dashboard did not come up on {url_for(host, port)}\n{log_tail(self.log_file)}".rstrip())

    def stop(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
        """End the dashboard and wait until the port is free. {"stopped", "pid"}."""
        pid = self.recorded_pid() or listening_pid(port)
        if pid is None:
            if health(host, port) is not None:
                raise DashboardError(f"a dashboard answers on {url_for(host, port)} but its process is not known here; stop it yourself")
            return {"stopped": False, "pid": None}
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + STOP_TIMEOUT
        while time.monotonic() < deadline:
            reap(pid)
            if not pid_alive(pid) and health(host, port) is None:
                self.pid_file.unlink(missing_ok=True)
                return {"stopped": True, "pid": pid}
            time.sleep(POLL)
        raise DashboardError(f"process {pid} did not stop within {STOP_TIMEOUT:.0f}s")

    def restart(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
        stopped = self.stop(host, port)
        started = self.start(host, port)
        return {**started, "stopped": stopped["stopped"], "previous": stopped["pid"]}


# `synapptic dashboard` itself; the module-level functions keep their names for the command and the tests.
def status(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    return Service(PID_FILE, LOG_FILE).status(host, port)


def start(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    return Service(PID_FILE, LOG_FILE).start(host, port)


def stop(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    return Service(PID_FILE, LOG_FILE).stop(host, port)


def restart(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    return Service(PID_FILE, LOG_FILE).restart(host, port)
