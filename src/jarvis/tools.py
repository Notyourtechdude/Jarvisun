"""Scoped tools. Arbitrary host shell execution is intentionally absent."""
import hashlib
import math
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager

from .config import Settings
from .quality import calculate
from .security import SECRET_PARTS, Broker, Denied


class ToolService:
    def __init__(self, settings: Settings, vault=None, focus=None):
        self.settings, self.broker, self.vault, self.focus = settings, Broker(settings), vault, focus

    @contextmanager
    def parent_fd(self, name: str, write: bool = False):
        path = self.broker.scoped_path(name, write)
        roots = [self.settings.workspace] if write else [self.settings.workspace, *self.settings.vault_roots]
        root = next(r for r in roots if path.is_relative_to(r))
        parts = path.relative_to(root).parts
        if not parts:
            raise ValueError("a file path is required")
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                if write:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_fd
            yield descriptor, parts[-1], path
        finally:
            os.close(descriptor)

    def read(self, name: str):
        with self.parent_fd(name) as (directory, filename, path):
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_size > self.settings.max_file_bytes
                        or info.st_nlink != 1):
                    raise ValueError("only bounded regular files are readable")
                content = handle.read(self.settings.max_file_bytes + 1)
        if len(content) > self.settings.max_file_bytes:
            raise ValueError("file exceeds configured size limit")
        return {"path": str(path), "content": content.decode("utf-8"), "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(), "verified": True}

    def write(self, name: str, content: str):
        if not isinstance(content, str):
            raise ValueError("content must be text")
        return self.write_bytes(name, content.encode("utf-8"), self.settings.max_file_bytes)

    def write_bytes(self, name: str, data: bytes, limit=100_000_000):
        if len(data) > limit:
            raise ValueError("content exceeds configured size limit")
        with self.parent_fd(name, True) as (directory, filename, path):
            temporary = f".jarvis-write-{uuid.uuid4().hex}"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
                check_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                with os.fdopen(check_fd, "rb") as handle:
                    actual = handle.read(len(data) + 1)
                if actual != data:
                    raise ValueError("write verification failed")
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "verified": True}

    def snapshot(self, name, destination, limit):
        with self.parent_fd(name) as (directory, filename, _):
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                    raise ValueError("input is not a bounded authorized regular file")
                with open(destination, "wb") as target:
                    total = 0
                    while chunk := source.read(65536):
                        total += len(chunk)
                        if total > limit:
                            raise ValueError("input grew beyond its size limit")
                        target.write(chunk)
        return destination

    def send_once(self, scope, key, text, handler):
        from .extensions.common import Journal
        journal = Journal(self.settings.data_dir / "effects.sqlite3")
        previous = journal.reserve(scope, key, {"text": text}, self.settings.daily_message_limit)
        return previous or journal.complete(scope, key, handler())

    def list_files(self, name="."):
        path = self.broker.scoped_path(name)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with os.scandir(descriptor) as entries:
                results = []
                for entry in entries:
                    if len(results) >= 200:
                        break
                    if entry.is_symlink() or entry.name.lower() in SECRET_PARTS or entry.name.startswith(".env"):
                        continue
                    try:
                        self.broker.scoped_path(path / entry.name)
                    except Denied:
                        continue
                    results.append({"name": entry.name, "directory": entry.is_dir(follow_symlinks=False)})
            return {"path": str(path), "files": sorted(results, key=lambda x: x["name"]), "verified": True}
        finally:
            os.close(descriptor)

    def validate(self, tool: str, arguments: dict):
        self.broker.authorize(tool)
        schemas = {
            "read_file": ({"path"}, {"path"}),
            "write_file": ({"path", "content"}, {"path", "content"}),
            "list_files": ({"path"}, set()),
            "calculate": ({"expression", "unit"}, {"expression"}),
            "vault_search": ({"query", "limit"}, {"query"}),
            "business_report": ({"path"}, {"path"}),
            "create_website": ({"path", "business", "title", "metrics"}, {"path", "business"}),
            "edit_video": ({"input", "output", "start", "duration", "captions"}, {"input", "output"}),
            "focus": ({"action", "task", "minutes"}, {"action"}),
            "notify": ({"text", "channel", "key", "urgent"}, {"text", "channel", "key"}),
            "social_publish": ({"text", "key"}, {"text", "key"}),
            "social_pause": ({"paused"}, {"paused"}),
            "transcribe": ({"path"}, {"path"}),
            "synthesize": ({"text", "output"}, {"text", "output"}),
            "generate_image": ({"prompt", "output"}, {"prompt", "output"}),
            "offline_render": ({"input", "output"}, {"input", "output"}),
            "sandbox_run": ({"argv", "timeout"}, {"argv"}),
        }
        if tool not in schemas:
            raise Denied("tool has no installed execution adapter")
        allowed, required = schemas[tool]
        if not isinstance(arguments, dict) or arguments.keys() - allowed or required - arguments.keys():
            raise ValueError("invalid or unknown tool arguments")
        text_fields = {"path", "input", "output", "content", "expression", "unit", "query", "business",
                       "title", "text", "prompt", "channel", "key", "action", "task"}
        for name, value in arguments.items():
            if name in text_fields and not isinstance(value, str):
                raise ValueError(f"{name} must be text")
            if name in text_fields and len(value) > self.settings.max_file_bytes:
                raise ValueError("text argument exceeds limit")
            if name in {"path", "input", "output"}:
                self.broker.scoped_path(value, name == "output" or tool in {"write_file", "create_website"})
        if tool == "calculate":
            calculate(arguments["expression"], arguments.get("unit", ""))
        if tool == "write_file" and len(arguments["content"].encode("utf-8")) > self.settings.max_file_bytes:
            raise ValueError("content exceeds byte limit")
        if tool == "create_website":
            from pathlib import Path

            from .extensions.common import bounded_text
            # Same pure text checks as the adapter, before any earlier plan step executes.
            bounded_text(arguments["business"], 1000)
            bounded_text(arguments.get("title") or "Business overview", 100)
            if (not isinstance(arguments.get("metrics", {}), dict)
                    or len(arguments.get("metrics", {})) > 12
                    or Path(arguments["path"]).suffix.lower() != ".html"):
                raise ValueError("invalid website title or metrics")
        if "limit" in arguments and (type(arguments["limit"]) is not int or not 1 <= arguments["limit"] <= 20):
            raise ValueError("query limit must be 1..20")
        if "minutes" in arguments and (type(arguments["minutes"]) is not int or not 1 <= arguments["minutes"] <= 180):
            raise ValueError("minutes must be 1..180")
        for name in ("start", "duration", "timeout"):
            if name in arguments and (type(arguments[name]) not in {int, float}
                                      or not math.isfinite(arguments[name]) or abs(arguments[name]) > 120):
                raise ValueError("time argument is invalid")
        if "key" in arguments and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", arguments["key"]):
            raise ValueError("effect key is invalid")
        if "paused" in arguments and type(arguments["paused"]) is not bool:
            raise ValueError("paused must be a boolean")
        if "urgent" in arguments and type(arguments["urgent"]) is not bool:
            raise ValueError("urgent must be an owner-configured boolean")

    def execute(self, tool: str, arguments: dict):
        self.validate(tool, arguments)
        if tool == "read_file":
            return self.read(arguments["path"])
        if tool == "write_file":
            return self.write(arguments["path"], arguments["content"])
        if tool == "list_files":
            return self.list_files(arguments.get("path", "."))
        if tool == "calculate":
            return calculate(arguments["expression"], arguments.get("unit", ""))
        if tool == "vault_search":
            if self.vault is None:
                raise ValueError("vault unavailable")
            return {"results": self.vault.search(arguments["query"], arguments.get("limit", 5)), "verified": True}
        if tool == "focus":
            if self.focus is None:
                raise ValueError("focus service unavailable")
            return self.focus.action(**arguments)
        if tool == "business_report":
            from .extensions.business import summarize_csv
            with tempfile.TemporaryDirectory(prefix="jarvis-business-") as directory:
                source = self.snapshot(arguments["path"], os.path.join(directory, "input.csv"), 10_000_000)
                return {**summarize_csv(source), "verified": True,
                        "note": "Calculated from supplied CSV; source accuracy/currency not independently verified."}
        if tool == "create_website":
            from .extensions.creative import write_website
            with tempfile.TemporaryDirectory(prefix="jarvis-website-") as directory:
                from pathlib import Path
                staged = Path(directory) / "site.html"
                result = write_website(staged, description=arguments["business"],
                                       title=arguments.get("title") or "Business overview",
                                       metrics=arguments.get("metrics", {}))
                receipt = self.write(arguments["path"], staged.read_text())
            return {**result, **receipt, "artifact": receipt["path"],
                    "note": "Artifact exists; business claims are owner-supplied."}
        if tool == "edit_video":
            from .extensions.creative import render_video
            with tempfile.TemporaryDirectory(prefix="jarvis-video-") as directory:
                from pathlib import Path
                source = self.snapshot(arguments["input"], Path(directory) / "input.mp4", 100_000_000)
                staged = Path(directory) / "output.mp4"
                result = render_video(source, staged, start=arguments.get("start", 0),
                                      duration=arguments.get("duration", 5), captions=arguments.get("captions", ()))
                receipt = self.write_bytes(arguments["output"], staged.read_bytes())
            return {**result, **receipt, "artifact": receipt["path"]}
        if tool in {"transcribe", "synthesize"}:
            from .extensions.voice import LocalVoice
            voice = LocalVoice(whisper_executable=os.environ.get("JARVIS_WHISPER_BIN"),
                               whisper_model=os.environ.get("JARVIS_WHISPER_MODEL"),
                               piper_executable=os.environ.get("JARVIS_PIPER_BIN"),
                               piper_model=os.environ.get("JARVIS_PIPER_MODEL"))
            if tool == "transcribe":
                with tempfile.TemporaryDirectory(prefix="jarvis-transcribe-") as directory:
                    from pathlib import Path
                    return voice.transcribe(self.snapshot(arguments["path"], Path(directory) / "input.wav", 20_000_000))
            with tempfile.TemporaryDirectory(prefix="jarvis-speech-") as directory:
                from pathlib import Path
                staged = Path(directory) / "output.wav"
                result = voice.synthesize(arguments["text"], staged)
                receipt = self.write_bytes(arguments["output"], staged.read_bytes(), 20_000_000)
            return {**result, **receipt, "artifact": receipt["path"]}
        if tool == "generate_image":
            from .extensions.creative import ImageGenerator
            with tempfile.TemporaryDirectory(prefix="jarvis-image-") as directory:
                from pathlib import Path
                staged = Path(directory) / "output.png"
                result = ImageGenerator(os.environ.get("JARVIS_IMAGE_BIN")).generate(arguments["prompt"], staged)
                receipt = self.write_bytes(arguments["output"], staged.read_bytes(), 20_000_000)
            return {**result, **receipt, "artifact": receipt["path"]}
        if tool == "notify":
            from .extensions.channels import Telegram, Twilio, WhatsApp
            text, channel = arguments["text"], arguments["channel"]
            if channel == "telegram":
                return self.send_once("telegram:" + self.settings.telegram_chat_id, arguments["key"], text,
                                      lambda: Telegram(owner_chat_id=self.settings.telegram_chat_id,
                                        owner_user_id=self.settings.telegram_user_id or None).send(text))
            if channel == "whatsapp":
                return self.send_once("whatsapp:" + self.settings.phone_number, arguments["key"], text,
                                      lambda: WhatsApp(phone_number_id=self.settings.whatsapp_phone_id,
                                                       owner_number=self.settings.phone_number).send(text))
            if channel == "phone":
                from .extensions.common import PolicyDenied
                call = Twilio(owner_number=self.settings.phone_number,
                              from_number=self.settings.twilio_from_number,
                              journal=self.settings.data_dir / "effects.sqlite3",
                              daily_call_cap=self.settings.daily_call_limit)
                try:
                    return call.call(text, key=arguments["key"])
                except PolicyDenied:
                    raise
                except ValueError:
                    # Provider acceptance may be uncertain; preserve that fact in fallback receipt.
                    result = self.send_once("telegram:" + self.settings.telegram_chat_id,
                                            arguments["key"][:110] + ":fallback", text,
                                            lambda: Telegram(owner_chat_id=self.settings.telegram_chat_id,
                                                owner_user_id=self.settings.telegram_user_id or None).send(text))
                    return {**result, "fallback": "phone_unavailable_or_uncertain", "call_delivery": "unknown"}
            raise ValueError("unknown configured notification channel")
        if tool in {"social_publish", "social_pause"}:
            from .extensions.social import SocialPublisher
            publisher = SocialPublisher(self.settings.data_dir / "effects.sqlite3",
                                        daily_cap=self.settings.daily_post_limit,
                                        forbidden_terms=self.settings.social_forbidden_terms)
            if tool == "social_pause":
                publisher.set_paused(arguments["paused"])
                return {"paused": arguments["paused"], "verified": True}
            return publisher.publish(arguments["text"], key=arguments["key"])
        if tool == "offline_render":
            if not self.settings.enable_browser:
                raise Denied("offline browser is disabled")
            from .computer import OfflineBrowser
            with tempfile.TemporaryDirectory(prefix="jarvis-browser-") as directory:
                from pathlib import Path
                source = self.snapshot(arguments["input"], Path(directory) / "input.html", 2_000_000)
                staged = Path(directory) / "output.png"
                result = OfflineBrowser(Path(directory)).render(source, staged)
                if not result.get("verified"):
                    return result
                receipt = self.write_bytes(arguments["output"], staged.read_bytes(), 20_000_000)
            return {**result, **receipt, "artifact": receipt["path"]}
        if tool == "sandbox_run":
            if not self.settings.sandbox_image:
                raise Denied("isolated terminal image is not configured")
            from .computer import DockerWorker
            return DockerWorker(self.settings.workspace, self.settings.sandbox_image).run(
                arguments["argv"], timeout=arguments.get("timeout", 30))
        raise ValueError("unsupported tool")
