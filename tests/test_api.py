import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from jarvis.app import create_app
    from jarvis.config import Settings
    from jarvis.models import LocalModel
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work",
                        vault_roots=[tmp_path / "work"], allowed_hosts=["testserver"])
    def unavailable(request):
        return httpx.Response(503)
    app = create_app(settings, model=LocalModel(settings, httpx.MockTransport(unavailable)))
    with TestClient(app) as session:
        session.headers["Authorization"] = "Bearer " + (settings.data_dir / "access-token").read_text()
        yield session


def test_api_requires_auth_but_liveness_does_not(client):
    assert client.get("/health", headers={"Authorization": ""}).json() == {"status": "ok"}
    assert client.get("/api/status", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/status").status_code == 200


def test_calculation_chat_works_without_model_and_unknowns_abstain(client):
    result = client.post("/api/chat", json={"message": "/calc 12*7"}).json()
    assert result["verified"]
    assert "84" in result["answer"]
    result = client.post("/api/chat", json={"message": "What did my accountant say?"}).json()
    assert not result["verified"]
    assert result["kind"] == "unavailable"


def test_file_index_search_flow_and_citation(client):
    write = client.post("/api/tools", json={"tool": "write_file", "arguments": {
        "path": "contract.md", "content": "Cancellation requires thirty days notice."},
        "idempotency_key": "write-contract"})
    assert write.status_code == 200, write.text
    assert write.json()["result"]["verified"]
    assert client.post("/api/vault/index", json={}).status_code == 200
    answer = client.post("/api/chat", json={"message": "/find Cancellation"}).json()
    assert answer["sources"]
    assert "thirty days" in answer["answer"]
    assert answer["kind"] == "evidence"
    assert answer["verified"]


def test_denied_tool_returns_safe_error_and_audit(client):
    response = client.post("/api/tools", json={"tool": "shell", "arguments": {"command": "echo unsafe"},
                                               "idempotency_key": "unsafe"})
    assert response.status_code == 403
    events = client.get("/api/audit").json()["events"]
    assert any(event["kind"] == "denied" for event in events)


def test_duplicate_tool_request_returns_same_receipt(client):
    payload = {"tool": "calculate", "arguments": {"expression": "6*7"}, "idempotency_key": "answer"}
    first = client.post("/api/tools", json=payload).json()
    second = client.post("/api/tools", json=payload).json()
    assert first["job"]["id"] == second["job"]["id"]
    payload["arguments"]["expression"] = "8*7"
    assert client.post("/api/tools", json=payload).status_code == 409


def test_focus_and_invalid_requests(client):
    response = client.post("/api/focus", json={"action": "start", "task": "Send report", "minutes": 5})
    assert response.json()["session"]["task"] == "Send report"
    assert client.post("/api/focus", json={"action": "complete"}).status_code == 200
    assert client.get("/api/focus").json()["moved_today"] == 1
    assert client.post("/api/chat", json={"message": "x", "admin": True}).status_code == 422


def test_no_wildcard_host_or_cross_origin_authentication(client):
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/chat", json={"message": "/calc 1+1"},
                       headers={"Origin": "https://evil.example"}).status_code == 403


def test_agent_validates_every_model_proposed_tool(tmp_path):
    from jarvis.app import create_app
    from jarvis.config import Settings
    from jarvis.models import LocalModel
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work",
                        allowed_hosts=["testserver"])
    def planning(request):
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '{"steps":[{"tool":"shell","arguments":{"command":"unsafe"}}]}'}}]})
    app = create_app(settings, model=LocalModel(settings, httpx.MockTransport(planning)))
    with TestClient(app) as session:
        session.headers["Authorization"] = "Bearer " + (settings.data_dir / "access-token").read_text()
        response = session.post("/api/chat", json={"message": "/do execute task", "idempotency_key": "plan"})
        assert response.status_code == 403
        assert session.get("/api/jobs").json()["jobs"] == []
