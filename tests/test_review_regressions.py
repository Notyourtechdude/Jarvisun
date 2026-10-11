import asyncio

import httpx
import pytest


def test_phone_policy_denial_does_not_trigger_telegram_fallback(tmp_path, monkeypatch):
    from jarvis.config import Settings
    from jarvis.extensions.channels import Telegram, Twilio
    from jarvis.extensions.common import PolicyDenied
    from jarvis.tools import ToolService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work", allowed_tools=["notify"],
                        phone_number="+971500000000", twilio_from_number="+12025550123", telegram_chat_id="123")
    settings.prepare()
    def denied(self, text, key):
        raise PolicyDenied("This workflow is paused")
    monkeypatch.setattr(Twilio, "call", denied)
    monkeypatch.setattr(Telegram, "send", lambda self, text: pytest.fail("policy must not trigger fallback"))
    with pytest.raises(PolicyDenied):
        ToolService(settings).execute("notify", {"text": "test", "channel": "phone", "key": "key"})


def test_agent_validates_all_step_arguments_before_writing(tmp_path):
    from jarvis.app import create_app
    from jarvis.config import Settings
    from jarvis.models import LocalModel
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    def plan(request):
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '{"steps":[{"tool":"write_file","arguments":{"path":"made.txt","content":"hello"}},'
            '{"tool":"read_file","arguments":{"wrong_argument":"made.txt"}}]}'}}]})
    app = create_app(settings, LocalModel(settings, httpx.MockTransport(plan)))
    with pytest.raises(ValueError):
        asyncio.run(app.state.core.chat("/do write and read a file", "schema"))
    assert not (settings.workspace / "made.txt").exists()


@pytest.mark.parametrize("website_args", [{"metrics": {str(i): i for i in range(13)}},
                         {"title": "   "}, {"title": "bad\u0000title"}, {"business": "bad\u0000business"},
                         {"path": "bad.txt"}])
def test_agent_checks_website_validation_before_any_effect(tmp_path, website_args):
    import json

    from jarvis.app import create_app
    from jarvis.config import Settings
    from jarvis.models import LocalModel
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    steps = [{"tool": "write_file", "arguments": {"path": "made.txt", "content": "hello"}},
             {"tool": "create_website", "arguments": {"path": "site.html", "business": "Owner facts",
                                                       **website_args}}]
    def plan(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"steps": steps})}}]})
    app = create_app(settings, LocalModel(settings, httpx.MockTransport(plan)))
    with pytest.raises(ValueError):
        asyncio.run(app.state.core.chat("/do write and build the site", "metric-budget"))
    assert not (settings.workspace / "made.txt").exists()


def test_uncertain_phone_fallback_is_journaled_once(tmp_path, monkeypatch):
    from jarvis.config import Settings
    from jarvis.extensions.channels import Telegram, Twilio
    from jarvis.extensions.common import ExtensionError
    from jarvis.tools import ToolService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work", allowed_tools=["notify"],
                        phone_number="+971500000000", twilio_from_number="+12025550123", telegram_chat_id="123")
    settings.prepare()
    sent = []
    def uncertain(self, text, key):
        raise ExtensionError("call delivery uncertain")
    def send(self, text):
        sent.append(text)
        return {"service": "telegram", "external_id": "1", "status": "accepted"}
    monkeypatch.setattr(Twilio, "call", uncertain)
    monkeypatch.setattr(Telegram, "send", send)
    tools = ToolService(settings)
    arguments = {"text": "test", "channel": "phone", "key": "same"}
    assert tools.execute("notify", arguments)["call_delivery"] == "unknown"
    assert tools.execute("notify", arguments)["external_id"] == "1"
    assert sent == ["test"]


def test_each_workflow_occurrence_gets_distinct_provider_key(tmp_path, monkeypatch):
    from jarvis.config import Settings
    from jarvis.extensions.channels import Telegram
    from jarvis.jobs import JobService
    from jarvis.store import Store
    from jarvis.tools import ToolService
    from jarvis.workflows import WorkflowService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work", allowed_tools=["notify"],
                        telegram_chat_id="123", quiet_start=0, quiet_end=0)
    settings.prepare()
    sent = []
    def send(self, text):
        sent.append(text)
        return {"service": "telegram", "external_id": str(len(sent)), "status": "accepted"}
    monkeypatch.setattr(Telegram, "send", send)
    store = Store(settings.data_dir / "state.sqlite3")
    service = WorkflowService(store, JobService(store), ToolService(settings))
    row = service.create("Notify", "notify", {"channel": "telegram", "text": "report", "key": "template"}, 60)
    service.tick()
    with store.connect() as db:
        db.execute("UPDATE workflows SET next_run=next_run-120 WHERE id=?", (row["id"],))
    service.tick()
    assert sent == ["report", "report"]


def test_shared_credential_policy_blocks_kube_azure_and_key_files(tmp_path):
    from jarvis.config import Settings
    from jarvis.tools import ToolService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    for name in (".kube/config", ".azure/msal_token_cache.json", "client-secret.json", "server.key"):
        path = settings.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret")
        with pytest.raises(ValueError):
            ToolService(settings).execute("read_file", {"path": name})


def test_extension_publish_does_not_follow_parent_swapped_to_symlink(tmp_path, monkeypatch):
    from jarvis.config import Settings
    from jarvis.tools import ToolService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    parent = settings.workspace / "safe"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    tools = ToolService(settings)
    original = tools.broker.scoped_path
    calls = []
    def swap(name, write=False):
        result = original(name, write)
        calls.append(1)
        if len(calls) == 2:
            parent.rename(settings.workspace / "old")
            parent.symlink_to(outside, target_is_directory=True)
        return result
    monkeypatch.setattr(tools.broker, "scoped_path", swap)
    with pytest.raises((ValueError, OSError)):
        tools.execute("create_website", {"path": "safe/escaped.html", "business": "Owner facts"})
    assert not (outside / "escaped.html").exists()
