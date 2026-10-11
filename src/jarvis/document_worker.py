"""PDF extraction with child-process resource limits, not a security sandbox."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_WORKER_SCRIPT = Path(__file__).absolute()
_MAX_OUTPUT = 8_000_000
_MAX_INPUT = 100_000_000


def _set_limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (_MAX_OUTPUT, _MAX_OUTPUT))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def extract_pdf(data: bytes, max_input_bytes: int, timeout: float = 10) -> str | None:
    """Send bounded bytes to isolated Python; retain no inherited credentials."""
    if (
        not isinstance(data, bytes)
        or type(max_input_bytes) is not int
        or not 1 <= max_input_bytes <= _MAX_INPUT
        or len(data) > max_input_bytes
    ):
        raise ValueError("PDF input exceeds its allowed byte limit")
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or not 0 < timeout <= 10
    ):
        raise ValueError("PDF time limit must be positive and at most 10 seconds")
    if os.name != "posix":
        raise ValueError("PDF worker resource isolation requires POSIX")
    if importlib.util.find_spec("pypdf") is None:
        return None
    environment = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    with tempfile.TemporaryDirectory(prefix="jarvis-document-") as directory:
        with tempfile.TemporaryFile(dir=directory) as output:
            child = subprocess.Popen(
                [sys.executable, "-I", str(_WORKER_SCRIPT), "--input-limit", str(max_input_bytes)],
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.DEVNULL,
                cwd=directory,
                env=environment,
                close_fds=True,
            )
            try:
                child.communicate(input=data, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                child.kill()
                child.communicate()
                raise ValueError("PDF parsing exceeded its time limit") from error
            except BaseException:
                child.kill()
                child.communicate()
                raise
            if output.seek(0, os.SEEK_END) > _MAX_OUTPUT:
                raise ValueError("PDF output exceeded its byte limit")
            if child.returncode != 0:
                raise ValueError("PDF parsing failed or exceeded resource limits")
            output.seek(0)
            result = output.read(_MAX_OUTPUT + 1)
    try:
        message = json.loads(result)
        if not isinstance(message, dict) or set(message) != {"text"}:
            raise ValueError("Invalid PDF worker output")
        text = message["text"]
        if text is not None and not isinstance(text, str):
            raise ValueError("Invalid PDF worker output")
        return text
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid PDF worker output") from error


def _worker() -> int:
    try:
        _set_limits()  # Before importing third-party parsers or reading document bytes.
        if len(sys.argv) != 3 or sys.argv[1] != "--input-limit":
            return 2
        limit = int(sys.argv[2])
        if not 1 <= limit <= _MAX_INPUT:
            return 2
        data = sys.stdin.buffer.read(limit + 1)
        if len(data) > limit:
            return 2
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=True)
        text = None
        if not reader.is_encrypted:
            parts = []
            size = 0
            for page in reader.pages[:200]:
                part = page.extract_text() or ""
                size += len(part.encode("utf-8")) + 1
                if size > _MAX_OUTPUT:
                    return 2
                parts.append(part)
            text = "\n".join(parts)
        encoded = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _MAX_OUTPUT:
            return 2
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception:
        # Provider/parser exception strings are not sent to the parent or logs.
        return 2


if __name__ == "__main__":
    raise SystemExit(_worker())
