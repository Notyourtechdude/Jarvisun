"""Owner-opted-in official Codex CLI suggestions using a separate auth home."""
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from jarvis.security import Denied

MAX_PROMPT_BYTES = 16_000
MAX_OUTPUT_BYTES = 128_000
RUN_TIMEOUT_SECONDS = 60


class CodexProvider:
    """Synchronous adapter; async callers should use run_in_threadpool.

    The wrapper never reads credentials or runs login. Only an explicit owner
    executable/auth home is accepted; no harness login is discovered or reused.
    """

    def __init__(self, workspace: Path, executable: Path | None, auth_home: Path | None,
                 enabled: bool = False):
        self.workspace = Path(workspace).absolute()
        self.enabled = enabled
        self.executable = Path(executable) if executable is not None else None
        self.auth_home = Path(auth_home) if auth_home is not None else None
        self._validate()

    def _validate(self):
        if self.executable is not None and not self.executable.is_absolute():
            raise Denied("owner Codex executable must be an absolute path")
        if self.auth_home is None:
            return
        if not self.auth_home.is_absolute():
            raise Denied("owner Codex auth home must be an absolute private path")
        protected = [Path('/workspace/.codex'), Path('/root/.codex'), Path('/home/agent/.codex')]
        inherited = os.environ.get('CODEX_HOME')
        if inherited:
            protected.append(Path(inherited).absolute())
        inherited_home = os.environ.get('HOME')
        if inherited_home:
            protected.append(Path(inherited_home) / '.codex')
        resolved = self.auth_home.resolve()
        if any(resolved.is_relative_to(path.resolve()) or path.resolve().is_relative_to(resolved)
               for path in protected):
            raise Denied("Codex auth home must be separate from harness authentication")
        if any(path.is_symlink() for path in [self.auth_home, *self.auth_home.parents]):
            raise Denied("owner Codex auth home cannot contain symlinks")
        if self.auth_home.exists():
            stat = self.auth_home.stat()
            if not self.auth_home.is_dir() or stat.st_mode & 0o077 or stat.st_uid != os.getuid():
                raise Denied("owner Codex auth home must be an owner-only private directory")
            # Check names only; never read auth/config file content.
            if any((self.auth_home / name).exists() for name in
                   ('AGENTS.md', 'AGENTS.override.md', 'skills', 'hooks.json')):
                raise Denied("Codex auth home must not contain implicit agent instructions or tools")

    def available(self) -> dict:
        self._validate()
        if not self.enabled:
            return {"available": False, "reason": "Owner Codex provider is disabled"}
        if self.executable is None or self.auth_home is None:
            return {"available": False, "reason": "Owner executable and separate auth home are required"}
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            return {"available": False, "reason": "Owner Codex executable is unavailable"}
        if not self.auth_home.is_dir():
            return {"available": False, "reason": "Owner must prepare a separate private Codex auth home"}
        return {"available": True, "provider": "codex", "authenticated": "not_checked",
                "reason": "Explicit owner configuration present; login and inference are not yet verified"}

    @staticmethod
    def _failure(reason):
        return {"completed": False, "verified": False, "provider": "codex", "content": "", "reason": reason}

    def _execute(self, prompt, task_dir):
        command = [str(self.executable), 'exec', '--sandbox', 'read-only', '--json',
                   '--skip-git-repo-check', '--ephemeral', '--ignore-user-config', '--ignore-rules',
                   '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
                   '--cd', str(task_dir), '-']
        environment = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'HOME': str(task_dir),
                       'CODEX_HOME': str(self.auth_home)}
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, shell=False, env=environment,
                                   cwd=task_dir, start_new_session=True)
        output = bytearray()
        pending = prompt.encode('utf-8')
        started = time.monotonic()
        failed = False
        try:
            os.set_blocking(process.stdin.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                selector.register(process.stdin, selectors.EVENT_WRITE)
                while selector.get_map():
                    remaining = RUN_TIMEOUT_SECONDS - (time.monotonic() - started)
                    if remaining <= 0:
                        failed = True
                        break
                    for key, event in selector.select(min(remaining, 0.1)):
                        if event == selectors.EVENT_WRITE:
                            try:
                                sent = os.write(key.fd, pending[:4096])
                                pending = pending[sent:]
                            except BrokenPipeError:
                                pending = b''
                            if not pending:
                                selector.unregister(key.fileobj)
                                process.stdin.close()
                        else:
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                                failed = True
                                break
                            output.extend(chunk)
                    if failed:
                        break
            if not failed:
                process.wait(timeout=max(0.1, RUN_TIMEOUT_SECONDS - (time.monotonic() - started)))
        except (OSError, subprocess.TimeoutExpired):
            failed = True
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()
        if failed or process.returncode != 0:
            return None
        return bytes(output)

    def run(self, prompt: str) -> dict:
        if not isinstance(prompt, str) or not prompt.strip():
            raise Denied("Codex prompt must be nonempty text")
        try:
            if len(prompt.encode('utf-8')) > MAX_PROMPT_BYTES:
                raise Denied("Codex prompt exceeds the byte budget")
        except UnicodeEncodeError:
            raise Denied("Codex prompt contains invalid text") from None
        status = self.available()
        if not status['available']:
            return self._failure(status['reason'])
        started = time.monotonic()
        try:
            # Outside repository/vault roots; never pass workspace files or mounts.
            with tempfile.TemporaryDirectory(prefix='jarvis-codex-task-', dir='/tmp') as directory:
                task_dir = Path(directory)
                task_dir.chmod(0o700)
                output = self._execute(prompt, task_dir)
            if output is None:
                return self._failure("Owner Codex execution failed, timed out, or exceeded output limits")
            text = None
            completed = False
            usage = {}
            for line in output.decode('utf-8').splitlines():
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                    return self._failure("Owner Codex returned malformed events")
                kind = event['type']
                if kind not in {'thread.started', 'turn.started', 'turn.completed', 'turn.failed',
                                'item.started', 'item.updated', 'item.completed', 'error'}:
                    return self._failure("Owner Codex returned an unsupported event protocol")
                if kind in {'error', 'turn.failed'}:
                    return self._failure("Owner Codex reported a failed turn")
                if kind == 'item.completed':
                    item = event.get('item')
                    if not isinstance(item, dict):
                        return self._failure("Owner Codex returned malformed item data")
                    if item.get('type') == 'error':
                        return self._failure("Owner Codex reported an item error")
                    if item.get('type') == 'agent_message':
                        value = item.get('text')
                        if not isinstance(value, str) or not value.strip():
                            return self._failure("Owner Codex returned an empty agent message")
                        text = value
                if kind == 'turn.completed':
                    completed = True
                    raw = event.get('usage', {})
                    if isinstance(raw, dict):
                        usage = {name: raw[name] for name in ('input_tokens', 'cached_input_tokens', 'output_tokens')
                                 if type(raw.get(name)) is int and 0 <= raw[name] <= 1_000_000_000}
            if not completed or text is None:
                return self._failure("Owner Codex returned no completed agent response")
            return {"completed": True, "verified": False, "provider": "codex", "content": text,
                    "usage": usage, "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                    "reason": "Suggestion returned; no Jarvis action or business result verified"}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return self._failure("Owner Codex executable or event protocol is unavailable")
