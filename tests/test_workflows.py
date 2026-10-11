import pytest


def test_recurring_workflow_persists_and_obeys_pause_and_due_time(tmp_path):
    from jarvis.config import Settings
    from jarvis.jobs import JobService
    from jarvis.store import Store
    from jarvis.tools import ToolService
    from jarvis.workflows import WorkflowService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    store = Store(settings.data_dir / "state.sqlite3")
    service = WorkflowService(store, JobService(store), ToolService(settings))
    row = service.create("Daily calculation", "calculate", {"expression": "12*7"}, 60)
    assert service.tick()[0]["result"]["value"] == "84"
    assert service.tick() == []
    service.pause(row["id"], True)
    assert service.list()[0]["paused"]
    assert WorkflowService(store, JobService(store), ToolService(settings)).tick() == []


def test_workflow_denies_unauthorized_actions_and_bad_intervals(tmp_path):
    from jarvis.config import Settings
    from jarvis.jobs import JobService
    from jarvis.store import Store
    from jarvis.tools import ToolService
    from jarvis.workflows import WorkflowService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    store = Store(settings.data_dir / "state.sqlite3")
    service = WorkflowService(store, JobService(store), ToolService(settings))
    with pytest.raises(ValueError):
        service.create("unsafe", "shell", {}, 60)
    with pytest.raises(ValueError):
        service.create("too frequent", "calculate", {"expression": "1"}, 1)


@pytest.mark.parametrize("hour,focus,urgent,deferred", [(23, False, False, True),
                         (5, False, False, True), (12, False, False, False),
                         (12, True, False, True), (23, True, True, False)])
def test_notifications_queue_during_quiet_hours_or_focus_and_allow_owner_urgency(
        tmp_path, monkeypatch, hour, focus, urgent, deferred):
    from datetime import datetime, timezone

    from jarvis.config import Settings
    from jarvis.extensions.channels import Telegram
    from jarvis.focus import FocusService
    from jarvis.jobs import JobService
    from jarvis.store import Store
    from jarvis.tools import ToolService
    from jarvis.workflows import WorkflowService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work", allowed_tools=["notify"],
                        timezone="UTC", telegram_chat_id="123")
    settings.prepare()
    store = Store(settings.data_dir / "state.sqlite3")
    focus_service = FocusService(store, settings.timezone)
    if focus:
        focus_service.action("start", "Protect this work", 5)
    class Clock:
        @staticmethod
        def now(zone):
            return datetime(2026, 10, 10, hour, tzinfo=timezone.utc).astimezone(zone)
    monkeypatch.setattr("jarvis.workflows.datetime", Clock)
    sent = []
    def send(self, text):
        sent.append(text)
        return {"external_id": "1", "status": "accepted"}
    monkeypatch.setattr(Telegram, "send", send)
    service = WorkflowService(store, JobService(store), ToolService(settings, focus=focus_service))
    row = service.create("Report", "notify", {"channel": "telegram", "text": "progress", "key": "report",
                                               "urgent": urgent}, 60)
    receipt = service.tick()[0]
    if deferred:
        assert receipt["deferred"] is True and sent == []
        assert not service.list()[0]["paused"]
        assert JobService(store).list() == []
        if focus:
            focus_service.action("pause")
        settings.quiet_start = settings.quiet_end = 0
        with store.connect() as db:
            db.execute("UPDATE workflows SET next_run=0 WHERE id=?", (row["id"],))
        assert service.tick()[0]["result"]["status"] == "accepted"
    else:
        assert receipt["result"]["status"] == "accepted"
    assert sent == ["progress"]
