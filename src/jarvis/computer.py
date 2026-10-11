"""Optional caller-authorized containers and network-disabled HTML screenshots."""
import os
import re
import selectors
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from jarvis.privacy import is_private
from jarvis.security import Denied

MAX_STDOUT_BYTES = 64_000
MAX_HTML_BYTES = 2_000_000


def _workspace(path: Path) -> Path:
    path = Path(path).absolute()
    _path(path, path)
    if not path.is_dir():
        raise Denied("authorized workspace must already be a directory")
    if "," in str(path) or "\n" in str(path):
        raise Denied("workspace path cannot contain mount option separators")
    return path


def _path(workspace: Path, path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        path = workspace / path
    if ".." in path.parts or not path.is_relative_to(workspace):
        raise Denied("computer path is outside authorized workspace")
    if any(is_private(part, directory=i < len(path.parts) - 1) for i, part in enumerate(path.parts)):
        raise Denied("private paths are excluded")
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise Denied("computer paths cannot contain symlinks")
    if not path.resolve().is_relative_to(workspace.resolve()):
        raise Denied("resolved computer path escaped workspace")
    return path


class DockerWorker:
    """Runs argv in a pinned local image. Authorization belongs to the caller."""

    def __init__(self, workspace: Path, image: str):
        self.workspace = _workspace(workspace)
        if not isinstance(image, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._/:\-]*@sha256:[a-fA-F0-9]{64}", image):
            raise Denied("container image must be pinned by SHA-256 digest")
        self.image = image

    def available(self) -> dict:
        docker = shutil.which("docker")
        if not docker:
            return {"available": False, "reason": "Docker executable is not installed"}
        # Read-only daemon/image inspection. No pull, build, or container mutation.
        try:
            for argv in ([docker, "info", "--format", "{{.ServerVersion}}"],
                         [docker, "image", "inspect", "--format", "{{.Id}}", self.image]):
                result = self._bounded_process(argv, timeout=5)
                if result["returncode"] != 0 or result["timed_out"] or result["truncated"]:
                    return {"available": False, "reason": "Docker daemon or pinned local image unavailable"}
            return {"available": True, "image": self.image}
        except OSError:
            return {"available": False, "reason": "Docker executable or daemon unavailable"}

    @staticmethod
    def _bounded_process(argv, timeout, cleanup=None):
        started = time.monotonic()
        body = bytearray()
        truncated = timed_out = False
        # No inherited credentials, proxy configuration, or application secrets.
        environment = {"PATH": os.defpath, "HOME": "/nonexistent", "LANG": "C.UTF-8"}
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, shell=False, env=environment)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        timed_out = True
                        break
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        available = MAX_STDOUT_BYTES - len(body)
                        body.extend(chunk[:available])
                        if len(chunk) > available:
                            truncated = True
                            break
                    if truncated:
                        break
            if timed_out or truncated:
                process.kill()
                if cleanup is not None:
                    cleanup()
            process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            if cleanup is not None:
                cleanup()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                if cleanup is not None:
                    cleanup()
                process.wait(timeout=5)
            process.stdout.close()
        return {"returncode": process.returncode, "stdout": body.decode("utf-8", errors="replace"),
                "truncated": truncated, "timed_out": timed_out}

    def run(self, argv: list[str], timeout=30) -> dict:
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 64
                or any(not isinstance(arg, str) or "\0" in arg or len(arg) > 8192 for arg in argv)
                or not argv[0] or Path(argv[0]).name in {"sh", "bash", "dash", "zsh", "fish", "cmd", "powershell"}):
            raise Denied("container command must be bounded argv without a shell entrypoint")
        if type(timeout) not in {int, float} or not 0 < timeout <= 120:
            raise Denied("container timeout must be between zero and 120 seconds")
        _workspace(self.workspace)
        # A mount exposes every file beneath its root; reject private material.
        seen = 0
        for parent, directories, files in os.walk(self.workspace, followlinks=False):
            for name in [*directories, *files]:
                seen += 1
                if seen > 10_000:
                    raise Denied("workspace exceeds the container inspection budget")
                _path(self.workspace, Path(parent) / name)
        status = self.available()
        if not status["available"]:
            return {"verified": False, "reason": status["reason"], "result": ""}
        docker = shutil.which("docker") or "docker"
        name = "jarvis-" + uuid.uuid4().hex
        user = f"{os.getuid() or 65534}:{os.getgid() or 65534}"
        command = [docker, "run", "--rm", "--pull=never", f"--name={name}", "--network=none",
                   "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
                   "--memory=512m", "--memory-swap=512m", "--pids-limit=64", "--cpus=1",
                   f"--user={user}", "--workdir=/workspace",
                   f"--mount=type=bind,source={self.workspace},target=/workspace",
                   "--tmpfs=/tmp:rw,noexec,nosuid,size=64m", self.image, *argv]

        def cleanup():
            # Killing only the CLI can leave daemon-owned work running.
            try:
                self._bounded_process([docker, "rm", "--force", name], timeout=5)
            except OSError:
                pass

        try:
            receipt = self._bounded_process(command, timeout, cleanup=cleanup)
        except OSError:
            return {"verified": False, "result": "", "reason": "Container execution unavailable"}
        verified = receipt["returncode"] == 0 and not receipt["truncated"] and not receipt["timed_out"]
        return {"verified": verified, "result": receipt.pop("stdout"), **receipt,
                "image": self.image, "reason": "Command exited successfully" if verified else
                "Container command failed, timed out, or exceeded output budget"}


class OfflineBrowser:
    """Render trusted generated HTML. No online navigation or desktop control."""

    def __init__(self, workspace: Path, executable_path: Path | None = None):
        self.workspace = _workspace(workspace)
        self.executable_path = str(executable_path) if executable_path is not None else (
            shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome"))
        if self.executable_path is not None and not os.access(self.executable_path, os.X_OK):
            raise Denied("configured browser executable is unavailable")

    def render(self, input_path: Path, output_path: Path) -> dict:
        input_path = _path(self.workspace, input_path)
        output_path = _path(self.workspace, output_path)
        if input_path.suffix.lower() not in {".html", ".htm"} or output_path.suffix.lower() != ".png":
            raise Denied("offline render requires HTML input and PNG output")
        if not input_path.is_file() or input_path.stat().st_size > MAX_HTML_BYTES:
            raise Denied("HTML input is missing or exceeds the byte budget")
        if output_path.exists() or not output_path.parent.is_dir():
            raise Denied("screenshot output must be a new file in an existing workspace directory")
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {"verified": False, "reason": "Optional Playwright package is unavailable"}
        blocked = 0
        try:
            html = input_path.read_text(encoding="utf-8")
            with sync_playwright() as engine:
                # Playwright's Chromium sandbox is not enabled here; no OS isolation claim.
                browser = engine.chromium.launch(headless=True, timeout=15_000,
                                                 executable_path=self.executable_path,
                                                 env={"PATH": os.defpath, "LANG": "C.UTF-8"})
                try:
                    context = browser.new_context(java_script_enabled=False, service_workers="block",
                                                  viewport={"width": 1280, "height": 800},
                                                  accept_downloads=False)
                    def abort(route):
                        nonlocal blocked
                        blocked += 1
                        route.abort("blockedbyclient")
                    context.route("**/*", abort)
                    # JS is disabled, but explicitly deny WebSockets where supported too.
                    if hasattr(context, "route_web_socket"):
                        context.route_web_socket("**/*", lambda socket: socket.close())
                    page = context.new_page()
                    page.set_default_timeout(10_000)
                    page.set_content(html, wait_until="load", timeout=10_000)
                    _path(self.workspace, output_path)
                    png = page.screenshot(type="png", full_page=False, timeout=10_000)
                    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                        return {"verified": False, "reason": "Browser returned an invalid screenshot"}
                    fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(png)
                    return {"verified": True, "path": str(output_path.relative_to(self.workspace)),
                            "bytes": len(png), "blocked_requests": blocked, "javascript_enabled": False}
                finally:
                    browser.close()
        except (PlaywrightError, OSError, UnicodeError):
            # Browser exception text can contain HTML, URLs, or local credential paths.
            return {"verified": False, "reason": "Offline browser or Chromium unavailable; render failed"}
