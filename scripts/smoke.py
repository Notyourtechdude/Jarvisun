"""Exercise the actual HTTP service with disposable state; never print tokens."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from jarvis.cli import initialize
from jarvis.config import Settings


def main():
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="jarvis-smoke-") as temporary:
        config = initialize(Path(temporary) / "app")
        settings = Settings.load(config)
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([sys.executable, "-m", "jarvis.cli", "serve", "--config", str(config),
                                        "--port", str(port)], stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
                    for _ in range(100):
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.HTTPError:
                            if process.poll() is not None:
                                raise RuntimeError("Service exited before readiness") from None
                        time.sleep(0.05)
                    else:
                        raise RuntimeError("Service did not become ready")
                    assert client.get("/api/status").status_code == 401
                    client.headers["Authorization"] = "Bearer " + (settings.data_dir / "access-token").read_text()
                    response = client.post("/api/chat", json={"message": "/calc 7*6"})
                    response.raise_for_status()
                    assert response.json()["answer"] == "42"
                    body = {"tool": "write_file", "arguments": {"path": "note.md", "content": "Launch needs tests."},
                            "idempotency_key": "smoke-write"}
                    first = client.post("/api/tools", json=body)
                    first.raise_for_status()
                    assert first.json()["result"]["verified"]
                    second = client.post("/api/tools", json=body)
                    second.raise_for_status()
                    assert second.json()["job"]["id"] == first.json()["job"]["id"]
                    client.post("/api/vault/index", json={}).raise_for_status()
                    results = client.get("/api/vault/search", params={"q": "Launch"}).json()["results"]
                    assert len(results) == 1 and "tests" in results[0]["text"]
                    client.post("/api/focus", json={"action": "start", "task": "Ship one thing", "minutes": 5}).raise_for_status()
                    client.post("/api/focus", json={"action": "complete"}).raise_for_status()
                    assert client.get("/api/focus").json()["moved_today"] == 1
                    assert client.get("/").status_code == 200
                    print(json.dumps({"status": "passed", "checks": ["authentication", "calculation", "verified-write",
                          "idempotency", "incremental-retrieval", "focus", "static-ui"], "state": "disposable"}))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
