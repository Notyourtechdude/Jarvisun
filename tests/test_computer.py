import json
from pathlib import Path

import pytest

from jarvis.computer import DockerWorker, OfflineBrowser
from jarvis.security import Denied

IMAGE = "python@sha256:" + "a" * 64


def test_worker_requires_pinned_image_and_real_scoped_workspace(tmp_path):
    with pytest.raises(Denied):
        DockerWorker(tmp_path, "python:latest")
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(Denied):
        DockerWorker(link, IMAGE)


def test_docker_command_has_scoped_resources_no_network_and_no_host_shell(tmp_path, monkeypatch):
    worker = DockerWorker(tmp_path, IMAGE)
    monkeypatch.setattr(worker, "available", lambda: {"available": True})
    recorded = []
    def bounded(argv, timeout, cleanup=None):
        recorded.append((argv, timeout))
        return {"returncode": 0, "stdout": "ok", "truncated": False, "timed_out": False}
    monkeypatch.setattr(worker, "_bounded_process", bounded)
    result = worker.run(["python", "-c", "print('ok')"])
    args = recorded[0][0]
    assert "--network=none" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--read-only" in args
    assert "--memory=512m" in args and "--pids-limit=64" in args and "--cpus=1" in args
    assert "--pull=never" in args
    assert sum(arg.startswith("--mount=") for arg in args) == 1
    assert f"source={tmp_path}" in next(arg for arg in args if arg.startswith("--mount="))
    assert "--tmpfs=/tmp:rw,noexec,nosuid,size=64m" in args
    assert IMAGE in args
    assert args[-3:] == ["python", "-c", "print('ok')"]
    assert result["verified"] is True
    assert result["result"] == "ok"


@pytest.mark.parametrize("argv", [[], "ls", ["/bin/sh", "-c", "cat /etc/passwd"],
                                   ["bash", "-c", "hello"], ["ls", "\x00bad"]])
def test_arbitrary_shell_or_invalid_commands_never_reach_docker(tmp_path, argv, monkeypatch):
    worker = DockerWorker(tmp_path, IMAGE)
    monkeypatch.setattr(worker, "available", lambda: pytest.fail("must validate before daemon check"))
    with pytest.raises(Denied):
        worker.run(argv)


def test_unavailable_docker_reports_unverified_without_simulated_execution(tmp_path, monkeypatch):
    worker = DockerWorker(tmp_path, IMAGE)
    monkeypatch.setattr(worker, "available", lambda: {"available": False, "reason": "Docker not installed"})
    result = worker.run(["python", "-V"])
    assert result["verified"] is False
    assert "Docker" in result["reason"]


def test_process_failure_timeout_or_output_overflow_never_claims_verified(tmp_path, monkeypatch):
    worker = DockerWorker(tmp_path, IMAGE)
    monkeypatch.setattr(worker, "available", lambda: {"available": True})
    for receipt in ({"returncode": 1, "stdout": "failed", "truncated": False, "timed_out": False},
                    {"returncode": -9, "stdout": "", "truncated": False, "timed_out": True},
                    {"returncode": -9, "stdout": "x", "truncated": True, "timed_out": False}):
        monkeypatch.setattr(worker, "_bounded_process", lambda *args, receipt=receipt, **kwargs: receipt)
        assert worker.run(["python", "-V"])["verified"] is False


def test_browser_rejects_private_outside_and_symlink_paths(tmp_path):
    browser = OfflineBrowser(tmp_path)
    input_path = tmp_path / "page.html"
    input_path.write_text("<h1>hi</h1>")
    for path in (tmp_path.parent / "outside.png", tmp_path / ".env", tmp_path / ".ssh" / "out.png"):
        with pytest.raises(Denied):
            browser.render(input_path, path)
    link = tmp_path / "linked.html"
    link.symlink_to(input_path)
    with pytest.raises(Denied):
        browser.render(link, tmp_path / "out.png")


def test_browser_real_offline_screenshot_and_request_blocking(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as engine:
        browser = OfflineBrowser(tmp_path)
        if not browser.executable_path and not Path(engine.chromium.executable_path).is_file():
            pytest.skip("Chromium not installed: live screenshot remains unavailable")
    page = tmp_path / "page.html"
    page.write_text('<html><body style="background:white"><h1>Real offline page</h1>'
                    '<img src="https://example.com/private.png">'
                    '<script>document.body.innerHTML="BAD";new WebSocket("wss://example.com");</script>'
                    '</body></html>')
    result = OfflineBrowser(tmp_path).render(page, tmp_path / "capture.png")
    assert result["verified"] is True, json.dumps(result)
    assert result["blocked_requests"] >= 1
    assert (tmp_path / "capture.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert result["javascript_enabled"] is False


def test_container_private_workspace_material_is_rejected(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("private")
    worker = DockerWorker(tmp_path, IMAGE)
    monkeypatch.setattr(worker, "available", lambda: pytest.fail("private material must be denied first"))
    with pytest.raises(Denied):
        worker.run(["python", "-V"])


def test_bounded_process_enforces_real_output_and_timeout():
    import sys
    worker = DockerWorker._bounded_process
    result = worker([sys.executable, "-c", "import sys;sys.stdout.write('x'*200000)"], 5)
    assert result["truncated"] is True
    assert len(result["stdout"]) == 64_000
    result = worker([sys.executable, "-c", "import time;time.sleep(2)"], .1)
    assert result["timed_out"] is True
    assert result["returncode"] != 0


def test_worker_subprocess_does_not_inherit_application_credentials(monkeypatch):
    import sys
    monkeypatch.setenv("JARVIS_MODEL_API_KEY", "private-test-marker")
    result = DockerWorker._bounded_process(
        [sys.executable, "-c", "import os;print('JARVIS_MODEL_API_KEY' in os.environ)"], 5)
    assert result["stdout"].strip() == "False"


def test_browser_does_not_overwrite_existing_outputs(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<h1>Trusted</h1>")
    output = tmp_path / "existing.png"
    output.write_bytes(b"owner-file")
    with pytest.raises(Denied):
        OfflineBrowser(tmp_path).render(page, output)
    assert output.read_bytes() == b"owner-file"
