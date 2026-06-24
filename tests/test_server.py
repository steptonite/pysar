"""On-demand whisper server lifecycle (lazy start)."""

from pysar import server


def test_ensure_running_noop_when_alive(monkeypatch):
    # Server already up → must NOT spawn anything, returns True instantly.
    monkeypatch.setattr(server, "is_alive", lambda: True)
    spawned = []
    monkeypatch.setattr(
        server.subprocess, "Popen", lambda *a, **k: spawned.append(a) or object()
    )
    assert server.ensure_running() is True
    assert spawned == []


def test_ensure_running_false_when_script_missing(monkeypatch, tmp_path):
    # Server down and the launcher script is absent → give up cleanly (False),
    # no spawn. (Caller then surfaces the usual "not running" error.)
    monkeypatch.setattr(server, "is_alive", lambda: False)
    monkeypatch.setattr(server, "_script", lambda: tmp_path / "nope.sh")
    spawned = []
    monkeypatch.setattr(
        server.subprocess, "Popen", lambda *a, **k: spawned.append(a) or object()
    )
    assert server.ensure_running(timeout=0.1) is False
    assert spawned == []


def test_shutdown_noop_when_nothing_started():
    # We never started a server → shutdown is a safe no-op (scripts/start.sh owns
    # the live one and cleans it up itself).
    server._proc = None
    server.shutdown()  # must not raise
