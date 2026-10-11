import base64
import io
import wave

import httpx
from fastapi.testclient import TestClient


def test_voice_requires_standing_permission_and_engine_is_not_faked(tmp_path):
    from jarvis.app import create_app
    from jarvis.config import Settings
    from jarvis.models import LocalModel
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work", allowed_hosts=["testserver"])
    model = LocalModel(settings, httpx.MockTransport(lambda r: httpx.Response(503)))
    with TestClient(create_app(settings, model)) as client:
        client.headers["Authorization"] = "Bearer " + (settings.data_dir / "access-token").read_text()
        assert client.post("/api/voice/chat", json={"audio": "bad"}).status_code == 403
        assert client.post("/api/voice/speak", json={"text": "Hello"}).status_code == 403


def test_voice_chat_has_bounded_local_audio_and_no_permanent_recording(tmp_path, monkeypatch):
    from jarvis.app import create_app
    from jarvis.config import Settings
    from jarvis.extensions.voice import LocalVoice
    from jarvis.models import LocalModel
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work", allowed_hosts=["testserver"],
                        allowed_tools=["transcribe"])
    data = io.BytesIO()
    with wave.open(data, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\0\0" * 1600)
    def transcribe(self, source):
        self._audio(source)
        return {"text": "/calc 6*7", "verified": "engine_output"}
    monkeypatch.setattr(LocalVoice, "transcribe", transcribe)
    with TestClient(create_app(settings, LocalModel(settings, httpx.MockTransport(lambda r: httpx.Response(503))))) as client:
        client.headers["Authorization"] = "Bearer " + (settings.data_dir / "access-token").read_text()
        response = client.post("/api/voice/chat", json={"audio": base64.b64encode(data.getvalue()).decode()})
        assert response.status_code == 200, response.text
        assert response.json()["answer"] == "42"
        assert list(settings.workspace.iterdir()) == []
        assert client.post("/api/voice/chat", json={"audio": "bad"}).status_code == 400
