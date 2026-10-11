"""Security and accessibility invariants for the dependency-free owner interface."""
import base64
import io
import json
import shutil
import wave
from html.parser import HTMLParser
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "jarvis" / "static"


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_owner_token_stays_only_in_memory_and_untrusted_content_is_text():
    script = (STATIC / "app.js").read_text()
    for forbidden in ("localStorage", "sessionStorage", "innerHTML", "document.cookie", "insertAdjacentHTML"):
        assert forbidden not in script
    assert "Authorization" in script
    assert "response.status === 401" in script
    assert "token = \"\"" in script
    assert "textContent" in script
    assert "AbortController" in script


def test_interface_has_local_assets_and_accessible_forms():
    page = Page((STATIC / "index.html").read_text())
    ids = {attrs["id"] for _, attrs in page.elements if "id" in attrs}
    labels = {attrs.get("for") for tag, attrs in page.elements if tag == "label"}
    for tag, attrs in page.elements:
        if tag in {"input", "textarea", "select"}:
            assert attrs.get("id") in labels or attrs.get("aria-label")
        if tag in {"script", "link"}:
            assert not attrs.get("src", attrs.get("href", "")).startswith(("http", "//"))
    assert {"connect-form", "chat-form", "focus-form", "vault-form", "tool-form"} <= ids
    password = next(attrs for tag, attrs in page.elements if tag == "input" and attrs.get("id") == "token")
    assert password["type"] == "password"
    assert any(attrs.get("role") == "alert" for _, attrs in page.elements)
    assert any(attrs.get("aria-live") == "polite" for _, attrs in page.elements)


def test_layout_supports_small_screens_and_reduced_motion():
    css = (STATIC / "style.css").read_text()
    assert "@media" in css
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css
    assert "overflow-wrap" in css


# Browser tests use contract fixtures only. The shipped interface never contains demo data.


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        try:
            instance = driver.chromium.launch(headless=True, executable_path=shutil.which("chromium"))
        except playwright.Error as error:
            pytest.skip(f"Install Chromium to run interface browser tests: {error}")
        yield instance
        instance.close()


@pytest.fixture
def owner_page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    state = {"unauthorized": False, "offline": False, "requests": [], "session": None, "moved_today": 0,
             "voice": {"available": False, "transcription": False, "synthesis": False, "reason": "No engines"},
             "workflows": [], "workflow_error": False}
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def route_request(route):
        request = route.request
        path = request.url.split("jarvis.test", 1)[1].split("?", 1)[0]
        if not path.startswith("/api/"):
            asset = STATIC / ("index.html" if path == "/" else path.lstrip("/"))
            if asset.is_file():
                types = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}
                route.fulfill(path=asset, content_type=types[asset.suffix], headers={
                    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:"})
            else:
                route.fulfill(status=404, body="Missing")
            return
        state["requests"].append({"path": path, "authorization": request.headers.get("authorization"),
                                  "body": request.post_data_json if request.method == "POST" else None})
        if state["offline"]:
            route.abort("failed")
            return
        if state["unauthorized"]:
            route.fulfill(status=401, json={"detail": "Invalid token"})
            return
        assert request.headers.get("authorization") == "Bearer owner-test-token"
        responses = {
            "/api/status": {"version": "0.1.0", "workspace": "/private/workspace", "model": {"available": False},
                            "vault": {"roots": ["/private/notes"]}, "voice": state["voice"], "capabilities": {
                                "files": {"available": True}, "calculator": {"available": True},
                                "vault": {"available": True}, "voice": {"available": False, "reason": "No engine"}}},
            "/api/chat": {"answer": '<img src=x onerror="window.injected=true"> Source-backed answer.',
                          "sources": [{"path": "/private/notes/brief.md", "chunk_id": 2}],
                          "verified": False, "kind": "retrieval"},
            "/api/vault/search": {"results": [{"source": "brief", "path": "/private/notes/brief.md", "chunk_id": 2,
                                                "text": "<script>window.injected=true</script> matching passage"}]},
            "/api/vault/graph": {"nodes": [{"id": "a", "path": "/private/notes/brief.md"},
                                          {"id": "b", "title": "<img src=x>", "type": "unresolved"}],
                                 "edges": [{"source": "a", "target": "b", "evidence": "[[link]]"}]},
            "/api/vault/index": {"indexed": 1, "removed": 0},
            "/api/jobs": {"jobs": [{"id": "job-1", "tool": "calculate", "state": "succeeded"}]},
            "/api/audit": {"events": [{"action": "tool", "timestamp": "2026-10-10T15:00:00Z"}]},
            "/api/tools": {"result": {"value": 84}, "job": {"id": "job-1", "state": "succeeded"}},
            "/api/voice/chat": {"answer": "The recorded answer.", "transcript": "Read the brief",
                                "verified": False, "kind": "inference", "sources": []},
            "/api/voice/speak": {"audio": base64.b64encode(recording_bytes()).decode(), "mime": "audio/wav"},
        }
        if path.startswith("/api/workflows"):
            if state["workflow_error"]:
                route.fulfill(status=400, json={"detail": "Workflow policy rejected the selected tool"})
                return
            if path.endswith("/pause"):
                state["workflows"][0]["paused"] = request.post_data_json["paused"]
                result = {"workflow": state["workflows"][0]}
            elif request.method == "POST":
                workflow = {**request.post_data_json, "id": "workflow-1", "paused": False, "next_run": 1800000000}
                state["workflows"].append(workflow)
                result = {"workflow": workflow}
            else:
                result = {"workflows": state["workflows"]}
        elif path == "/api/focus":
            if request.method == "POST":
                body = request.post_data_json
                action = body["action"]
                if action == "start":
                    state["session"] = {"task": body["task"], "status": "active", "remaining_seconds": body["minutes"] * 60}
                else:
                    state["session"]["status"] = {"pause": "paused", "resume": "active", "complete": "completed"}[action]
                    if action == "complete":
                        state["moved_today"] += 1
            result = {"session": state["session"], "moved_today": state["moved_today"]}
        else:
            result = responses[path]
        route.fulfill(json=result)

    context.route("http://jarvis.test/**", route_request)
    page.goto("http://jarvis.test/")
    yield page, state, errors
    context.close()
    assert errors == []


def connect_owner(page):
    page.locator("#token").fill("owner-test-token")
    page.locator("#connect-form button").click()
    page.locator("#connection-state.connected").wait_for()
    page.locator("#focus-progress").filter(has_text="completed focus sessions today").wait_for()


def test_browser_chat_sources_safe_rendering_and_disconnect(owner_page):
    page, state, _ = owner_page
    assert page.locator("#chat-form button").is_disabled()
    connect_owner(page)
    assert page.locator("#token").input_value() == ""
    assert "unavailable" in page.locator("#model-status").inner_text()
    page.locator("#message").fill("Find the project brief")
    page.locator("#message").press("Control+Enter")
    page.locator(".message.assistant").wait_for()
    assert "Unverified" in page.locator(".message-meta").inner_text()
    assert page.locator(".message.assistant img").count() == 0
    page.locator(".message.assistant .sources summary").click()
    assert "/private/notes/brief.md" in page.locator(".message.assistant .sources").inner_text()
    assert page.evaluate("window.injected") is None
    assert page.evaluate("[Object.keys(localStorage).length, Object.keys(sessionStorage).length]") == [0, 0]
    page.locator("#disconnect").click()
    assert page.locator(".message").count() == 0
    assert "/private" not in page.locator("body").inner_text()
    assert page.locator("#chat-form button").is_disabled()
    assert all(request["authorization"] == "Bearer owner-test-token" for request in state["requests"])


def test_browser_focus_vault_tools_and_receipts(owner_page):
    page, state, _ = owner_page
    connect_owner(page)
    page.locator("#focus-task").fill("Open the project brief")
    page.locator("#focus-form button").click()
    page.locator("#focus-session").wait_for(state="visible")
    assert page.locator("#focus-clock").inner_text().startswith("15:")
    page.locator("#focus-toggle").click()
    page.locator("#focus-toggle").filter(has_text="Resume").wait_for()
    page.locator("#focus-toggle").click()
    page.locator("#focus-toggle").filter(has_text="Pause").wait_for()
    page.locator("#focus-complete").click()
    page.locator("#focus-form").wait_for(state="visible")
    assert "1 completed focus session today" in page.locator("#focus-progress").inner_text()
    page.locator('[data-view="vault"]').click()
    page.locator("#vault-query").fill("brief")
    page.locator("#vault-form button").click()
    page.locator(".result-item").wait_for()
    assert page.locator(".result-item script").count() == 0
    page.locator("#load-graph").click()
    page.locator("#vault-graph svg").wait_for()
    assert page.locator("#vault-graph line").count() == 1
    assert "<img src=x>" in page.locator("#graph-sources").inner_text()
    page.locator('[data-view="workspace"]').click()
    assert "Unavailable · No engine" in page.locator("#capabilities").inner_text()
    page.locator("#tool-name").select_option("calculate")
    page.locator("#tool-arguments").fill("not JSON")
    page.locator("#tool-form button").click()
    page.locator("#notice").filter(has_text="valid JSON").wait_for()
    page.locator("#tool-arguments").fill(json.dumps({"expression": "24 * 3 + 12"}))
    page.locator("#tool-form button").click()
    page.locator("#tool-output").filter(has_text="job-1").wait_for()
    tool_request = next(request for request in state["requests"] if request["path"] == "/api/tools")
    assert tool_request["body"]["idempotency_key"]
    page.locator('[data-view="activity"]').click()
    page.locator("#refresh-activity").click()
    page.locator("#jobs summary").wait_for()
    assert "succeeded" in page.locator("#jobs").inner_text()
    assert page.locator("#audit summary").count() == 1


def test_browser_network_error_and_unauthorized_clear_private_data(owner_page):
    page, state, _ = owner_page
    connect_owner(page)
    state["offline"] = True
    page.locator("#message").fill("Try this message")
    page.locator("#chat-form button").click()
    page.locator("#notice").filter(has_text="Could not reach Jarvis").wait_for()
    assert page.locator("#message").input_value() == "Try this message"
    state["offline"] = False
    page.locator("#chat-form button").click()
    page.locator(".message.assistant").wait_for()
    attempts = [request["body"] for request in state["requests"] if request["path"] == "/api/chat"]
    assert len(attempts) == 2
    assert attempts[0]["idempotency_key"] == attempts[1]["idempotency_key"]
    state["unauthorized"] = True
    page.locator("#refresh-status").click()
    page.locator("#connection-panel").wait_for(state="visible")
    assert page.locator("#token").input_value() == ""
    assert page.locator("#message").input_value() == ""
    assert page.locator(".message").count() == 0
    assert "rejected or expired" in page.locator("#notice").inner_text()
    assert page.locator("#chat-form button").is_disabled()


@pytest.mark.parametrize("width", [320, 768, 1024, 1440])
def test_browser_responsive_layout_and_keyboard_focus(owner_page, width):
    page, _, _ = owner_page
    page.set_viewport_size({"width": width, "height": 900})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    connect_owner(page)
    for view in ["conversation", "vault", "workspace", "activity"]:
        page.locator(f'[data-view="{view}"]').click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.evaluate("document.activeElement.tagName") == "H2"
        if view == "workspace":
            page.locator("#workflow-controls > summary").click()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        if view == "conversation":
            page.locator("#voice-controls > summary").click()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator('[data-view="conversation"]').focus()
    page.keyboard.press("Enter")
    assert page.locator("#view-conversation").is_visible()


def test_live_app_serves_the_interface_assets_and_protects_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from jarvis.app import create_app
    from jarvis.config import Settings

    for name in ("JARVIS_WHISPER_BIN", "JARVIS_WHISPER_MODEL", "JARVIS_PIPER_BIN", "JARVIS_PIPER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "workspace",
                        allowed_hosts=["testserver"])
    with TestClient(create_app(settings)) as client:
        index = client.get("/")
        assert index.status_code == 200
        page = Page(index.text)
        assets = [attrs.get("src", attrs.get("href")) for tag, attrs in page.elements
                  if tag in {"script", "link"}]
        assert len(assets) == 2
        for asset in assets:
            response = client.get(asset)
            assert response.status_code == 200
            assert "text/" in response.headers["content-type"]
        assert client.get("/api/status").status_code == 401
        assert client.get("/health").json() == {"status": "ok"}
        token = (settings.data_dir / "access-token").read_text()
        status = client.get("/api/status", headers={"Authorization": f"Bearer {token}"}).json()
        voice = status.get("voice", status["capabilities"]["voice"])
        assert voice["available"] is False
        assert voice["transcription"] is False
        assert voice["synthesis"] is False
        assert "script-src 'self'" in index.headers["content-security-policy"]
        assert "media-src 'self' blob:" in index.headers["content-security-policy"]


def test_browser_disconnect_aborts_inflight_private_response(owner_page):
    page, _, _ = owner_page
    connect_owner(page)
    pending = []
    page.route("http://jarvis.test/api/chat", lambda route: pending.append(route))
    page.locator("#message").fill("A private request")
    page.locator("#chat-form button").click()
    page.wait_for_timeout(50)
    assert len(pending) == 1
    page.locator("#disconnect").click()
    pending[0].fulfill(json={"answer": "A late private response", "verified": False, "sources": []})
    page.wait_for_timeout(50)
    assert "A late private response" not in page.locator("body").inner_text()
    assert "A private request" not in page.locator("body").inner_text()
    assert page.locator("#chat-form button").is_disabled()


def test_optional_voice_and_workflows_have_explicit_accessible_controls():
    page = Page((STATIC / "index.html").read_text())
    ids = {attrs["id"] for _, attrs in page.elements if "id" in attrs}
    assert {"voice-controls", "voice-form", "voice-file", "voice-state", "workflow-form",
            "workflow-name", "workflow-interval", "workflows", "refresh-workflows"} <= ids
    script = (STATIC / "app.js").read_text()
    assert "URL.revokeObjectURL" in script
    assert "speechSynthesis" not in script
    assert "SpeechRecognition" not in script


def recording_bytes(rate=16000, channels=1, seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(b"\0\0" * rate * channels * seconds)
    return output.getvalue()


def test_browser_workflow_schedule_pause_resume_and_policy_error(owner_page):
    page, state, _ = owner_page
    connect_owner(page)
    page.locator('[data-view="workspace"]').click()
    page.locator("#workflow-controls summary").first.click()
    assert page.locator("#workflow-form button").is_disabled()
    page.locator("#tool-name").select_option("calculate")
    page.locator("#tool-arguments").fill('{"expression":"6 * 7"}')
    page.locator("#workflow-name").fill("Morning calculation")
    page.locator("#workflow-interval").fill("60")
    page.locator("#workflow-form button").click()
    page.locator(".workflow-row").wait_for()
    assert state["workflows"][0]["arguments"] == {"expression": "6 * 7"}
    assert "Active" in page.locator(".workflow-row").inner_text()
    page.get_by_role("button", name="Pause workflow Morning calculation", exact=True).click()
    page.get_by_role("button", name="Resume workflow Morning calculation", exact=True).wait_for()
    assert state["workflows"][0]["paused"] is True
    page.get_by_role("button", name="Resume workflow Morning calculation", exact=True).click()
    page.get_by_role("button", name="Pause workflow Morning calculation", exact=True).wait_for()
    state["workflow_error"] = True
    page.locator("#refresh-workflows").click()
    page.locator("#notice").filter(has_text="Workflow policy rejected").wait_for()
    assert "Could not refresh" in page.locator("#workflow-feedback").inner_text()
    page.locator("#disconnect").click()
    assert page.locator(".workflow-row").count() == 0
    assert page.locator("#workflow-name").input_value() == ""


def test_browser_voice_upload_local_speech_and_audio_cleanup(owner_page):
    page, state, _ = owner_page
    state["voice"] = {"available": True, "transcription": True, "synthesis": True, "reason": "Local test engines"}
    page.evaluate("""() => {
        window.audioUrlsCreated = []; window.audioUrlsRevoked = [];
        const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = (blob) => { const url = create(blob); window.audioUrlsCreated.push(url); return url; };
        URL.revokeObjectURL = (url) => { window.audioUrlsRevoked.push(url); revoke(url); };
    }""")
    connect_owner(page)
    page.locator("#voice-controls summary").click()
    page.locator("#voice-file").set_input_files({"name": "recording.wav", "mimeType": "audio/wav",
                                                "buffer": recording_bytes()})
    page.locator("#voice-form button").click()
    page.locator(".message.assistant").filter(has_text="The recorded answer").wait_for()
    assert page.locator("#voice-file").input_value() == ""
    voice_request = next(request for request in state["requests"] if request["path"] == "/api/voice/chat")
    assert base64.b64decode(voice_request["body"]["audio"])[:4] == b"RIFF"
    page.locator(".speech-button").click()
    page.locator(".speech-audio").wait_for()
    assert page.locator(".speech-audio").get_attribute("src").startswith("blob:")
    assert len(page.evaluate("window.audioUrlsCreated")) == 1
    page.locator("#disconnect").click()
    assert page.locator("audio").count() == 0
    assert page.evaluate("window.audioUrlsCreated") == page.evaluate("window.audioUrlsRevoked")


def test_browser_voice_unavailable_and_invalid_recording_are_explicit(owner_page):
    page, state, _ = owner_page
    connect_owner(page)
    page.locator("#voice-controls summary").click()
    assert "Transcription: unavailable" in page.locator("#voice-state").inner_text()
    assert page.locator("#voice-form button").is_disabled()
    assert page.locator("#voice-file").is_disabled()
    state["voice"] = {"available": True, "transcription": True, "synthesis": False}
    page.locator("#refresh-status").click()
    page.locator("#voice-form button").wait_for(state="visible")
    page.locator("#voice-file").set_input_files({"name": "wrong-format.wav", "mimeType": "audio/wav",
                                                "buffer": recording_bytes(rate=44100, channels=2)})
    page.locator("#voice-form button").click()
    page.locator("#notice").filter(has_text="16 kHz, mono").wait_for()
    page.locator("#voice-file").set_input_files({"name": "too-long.wav", "mimeType": "audio/wav",
                                                "buffer": recording_bytes(seconds=31)})
    page.locator("#voice-form button").click()
    page.locator("#notice").filter(has_text="at most 30 seconds").wait_for()
    assert not any(request["path"] == "/api/voice/chat" for request in state["requests"])
