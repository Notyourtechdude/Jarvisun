import pytest


def test_broker_denies_unauthorized_and_escaped_paths(tmp_path):
    from jarvis.config import Settings
    from jarvis.security import Broker, Denied
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    broker = Broker(settings)
    for path in ("../other", ".env", str(tmp_path / "other")):
        with pytest.raises(Denied):
            broker.scoped_path(path)
    with pytest.raises(Denied):
        broker.authorize("shell")
    (settings.workspace / "link").symlink_to(tmp_path)
    with pytest.raises(Denied):
        broker.scoped_path("link/secret")


def test_calculator_exact_arithmetic_and_rejects_executable_input():
    from jarvis.quality import calculate
    assert calculate("0.1 + 0.2", "USD")["value"] == "0.3"
    assert calculate("(12 * 5) / 4")["value"] == "15"
    for expr in ("__import__('os')", "10**1000000", "1/0", "[1]*10000"):
        with pytest.raises(ValueError):
            calculate(expr)


def test_expected_returns_selects_net_value_with_explicit_inputs():
    from jarvis.quality import rank_options
    rows = rank_options([
        {"name": "A", "benefit": 100, "probability": .8, "cost": 40, "expected_loss": 5},
        {"name": "B", "benefit": 70, "probability": .9, "cost": 10, "expected_loss": 3},
    ])
    assert rows[0]["name"] == "B"
    assert rows[0]["net_value"] == "50"
    with pytest.raises(ValueError):
        rank_options([{"name": "bad", "benefit": 100, "probability": 2}])


def test_audit_redacts_secrets_and_does_not_store_file_contents(tmp_path):
    from jarvis.store import Store
    store = Store(tmp_path / "state.sqlite3")
    store.audit("tool", {"api_key": "verysecret", "nested": {"password": "secret"}, "bytes": 10})
    event = store.events()[0]
    assert "verysecret" not in str(event)
    assert "password': 'secret" not in str(event)
    assert event["details"]["bytes"] == 10


def test_jobs_idempotency_conflicts_and_restart_preserve_outcomes(tmp_path):
    from jarvis.jobs import JobConflict, JobService
    from jarvis.store import Store
    state = tmp_path / "jobs.sqlite3"
    jobs = JobService(Store(state))
    count = []
    def handler():
        count.append(1)
        return {"verified": True, "value": 7}
    first = jobs.run("calculate", {"expression": "3+4"}, "stable-key", handler)
    second = JobService(Store(state)).run("calculate", {"expression": "3+4"}, "stable-key", handler)
    assert first["result"] == second["result"]
    assert len(count) == 1
    with pytest.raises(JobConflict):
        jobs.run("calculate", {"expression": "9+4"}, "stable-key", handler)


def test_interrupted_job_not_blindly_retried(tmp_path):
    from jarvis.jobs import JobConflict, JobService
    from jarvis.store import Store
    jobs = JobService(Store(tmp_path / "jobs.sqlite3"))
    job, _ = jobs.reserve("notify", {}, "crash-key")
    assert jobs.claim(job["id"])
    assert jobs.recover() == 1
    with pytest.raises(JobConflict):
        jobs.run("notify", {}, "crash-key", lambda: pytest.fail("must not retry"))


def test_focus_session_persists_pause_resume_and_meaningful_completions(tmp_path):
    from jarvis.focus import FocusService
    from jarvis.store import Store
    path = tmp_path / "focus.sqlite3"
    focus = FocusService(Store(path))
    started = focus.action("start", "Send one client email", 10)
    assert started["session"]["status"] == "active"
    assert focus.action("pause")["session"]["status"] == "paused"
    focus = FocusService(Store(path))
    assert focus.action("resume")["session"]["status"] == "active"
    assert focus.action("complete")["session"]["status"] == "completed"
    assert focus.status()["moved_today"] == 1


def test_scoped_file_tools_verify_write_and_reject_symlinks(tmp_path):
    from jarvis.config import Settings
    from jarvis.tools import ToolService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    tools = ToolService(settings)
    written = tools.execute("write_file", {"path": "project/note.md", "content": "hello"})
    assert written["verified"]
    assert tools.execute("read_file", {"path": "project/note.md"})["content"] == "hello"
    (settings.workspace / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        tools.execute("write_file", {"path": "escape/owned", "content": "unsafe"})
    assert not (tmp_path / "owned").exists()


def test_tool_arguments_are_strict_and_unsupported_tools_fail(tmp_path):
    from jarvis.config import Settings
    from jarvis.tools import ToolService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    tools = ToolService(settings)
    with pytest.raises(ValueError):
        tools.execute("write_file", {"path": "a", "content": "b", "shell": True})
    with pytest.raises(ValueError):
        tools.execute("terminal", {"command": "rm -rf /"})
