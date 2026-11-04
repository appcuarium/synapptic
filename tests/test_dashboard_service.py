"""`synapptic dashboard start|stop|restart|status`: the dashboard as a background service. start spawns the
server detached, records its pid and waits for /api/health; stop ends it and waits for the port; status
says what answers. A bare `synapptic dashboard` is start."""

import socket

import pytest
from click.testing import CliRunner

from synapptic.cli import cli
from synapptic.dashboard import service


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def records(tmp_path, monkeypatch):
    # boundary: the pid and log files live under tmp_path, never under the user's ~/.synapptic
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "dashboard.pid")
    monkeypatch.setattr(service, "LOG_FILE", tmp_path / "dashboard.log")
    return tmp_path


def test_start_records_the_pid_and_stop_frees_the_port(records):
    port = free_port()
    try:
        started = service.start("127.0.0.1", port)
        assert started["started"] is True and started["pid"] > 0 and started["url"] == f"http://127.0.0.1:{port}/"
        assert (records / "dashboard.pid").read_text().strip() == str(started["pid"])
        assert service.health("127.0.0.1", port) == {"status": "ok", "protocol": 1}
        again = service.start("127.0.0.1", port)
        assert again["started"] is False and again["pid"] == started["pid"]
        shown = service.status("127.0.0.1", port)
        assert shown["running"] is True and shown["pid"] == started["pid"] and shown["recorded"] is True
    finally:
        stopped = service.stop("127.0.0.1", port)
    assert stopped == {"stopped": True, "pid": started["pid"]}
    assert service.health("127.0.0.1", port) is None and not (records / "dashboard.pid").exists()
    assert "Finished server process" in (records / "dashboard.log").read_text()


def test_stop_and_status_on_a_free_port_say_not_running(records):
    port = free_port()
    assert service.stop("127.0.0.1", port) == {"stopped": False, "pid": None}
    assert service.status("127.0.0.1", port)["running"] is False
    result = CliRunner().invoke(cli, ["dashboard", "status", "--port", str(port)])
    assert result.exit_code == 0 and result.output.startswith("Dashboard: not running")
    result = CliRunner().invoke(cli, ["dashboard", "stop", "--port", str(port)])
    assert result.exit_code == 0 and result.output == "Dashboard: not running\n"


def test_the_bare_command_is_start(records):
    port = free_port()
    try:
        result = CliRunner().invoke(cli, ["dashboard", "--port", str(port)])
        assert result.exit_code == 0 and result.output.startswith(f"Dashboard: started at http://127.0.0.1:{port}/ (pid ")
        assert service.health("127.0.0.1", port) is not None
    finally:
        service.stop("127.0.0.1", port)
