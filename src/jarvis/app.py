"""Authenticated local service. Static UI contains no private data or token."""
import asyncio
import base64
import binascii
import os
import secrets
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import Settings
from .focus import FocusService
from .jobs import JobConflict, JobService
from .models import LocalModel, ModelUnavailable
from .orchestrator import Orchestrator
from .quality import rank_options
from .security import Denied, owner_token
from .store import Store
from .tools import ToolService
from .vault import Vault
from .workflows import WorkflowService


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatBody(StrictBody):
    message: str = Field(min_length=1, max_length=6000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)


class ToolBody(StrictBody):
    tool: str = Field(min_length=1, max_length=40)
    arguments: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=100)


class FocusBody(StrictBody):
    action: str
    task: str = Field(default="", max_length=500)
    minutes: int = Field(default=15, ge=1, le=180)


class VisionBody(StrictBody):
    prompt: str = Field(default="Describe the observed image. State uncertainty.", max_length=2000)
    image: str = Field(min_length=1, max_length=7_000_000)


class RankBody(StrictBody):
    options: list[dict] = Field(min_length=1, max_length=20)


class WorkflowBody(StrictBody):
    name: str = Field(min_length=1, max_length=100)
    tool: str = Field(min_length=1, max_length=40)
    arguments: dict = Field(default_factory=dict)
    interval: int = Field(default=3600, ge=60, le=2592000)


class PauseBody(StrictBody):
    paused: bool


class VoiceBody(StrictBody):
    audio: str = Field(min_length=1, max_length=4_000_000)


class SpeakBody(StrictBody):
    text: str = Field(min_length=1, max_length=1000)


def create_app(settings: Settings | None = None, model=None):
    settings = settings or Settings()
    token = owner_token(settings)
    store = Store(settings.data_dir / "state.sqlite3")
    jobs = JobService(store)
    vault = Vault(settings.vault_roots, settings.data_dir / "vault.sqlite3", settings.max_file_bytes)
    focus = FocusService(store, settings.timezone)
    tools = ToolService(settings, vault=vault, focus=focus)
    model = model or LocalModel(settings)
    core = Orchestrator(model, vault, tools, jobs, store)
    workflows = WorkflowService(store, jobs, tools)

    @asynccontextmanager
    async def lifespan(app):
        jobs.recover()
        stop = asyncio.Event()

        async def worker():
            last_index = time.monotonic()
            while not stop.is_set():
                try:
                    await run_in_threadpool(workflows.tick)
                    if (settings.auto_index_seconds and settings.vault_roots
                            and time.monotonic() - last_index >= settings.auto_index_seconds):
                        await run_in_threadpool(vault.index)
                        last_index = time.monotonic()
                except Exception:
                    store.audit("worker_error", {"reason": "Background task failed; bounded retry next tick"})
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
        task = asyncio.create_task(worker())
        yield
        stop.set()
        await task

    app = FastAPI(title="Jarvis", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.jobs = settings, store, jobs
    app.state.vault, app.state.core, app.state.tools = vault, core, tools
    app.state.workflows = workflows
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.middleware("http")
    async def perimeter(request: Request, call_next):
        if request.url.path.startswith("/api"):
            supplied = request.headers.get("authorization", "")
            if not secrets.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                return JSONResponse({"detail": "Owner authentication required"}, status_code=401)
            origin = request.headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.netloc != request.headers.get("host") or parsed.scheme != request.url.scheme:
                    return JSONResponse({"detail": "Cross-origin requests are denied"}, status_code=403)
            if request.method in {"POST", "PUT", "PATCH"}:
                limit = {"/api/vision": 7_100_000, "/api/voice/chat": 4_100_000}.get(
                    request.url.path, 256_000)
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > limit:
                        return JSONResponse({"detail": "Request exceeds byte limit"}, status_code=413)
                    body.extend(chunk)
                request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.exception_handler(Denied)
    async def denied(request, error):
        store.audit("denied", {"route": request.url.path})
        return JSONResponse({"detail": str(error)}, status_code=403)

    @app.exception_handler(JobConflict)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ModelUnavailable)
    async def unavailable(request, error):
        return JSONResponse({"detail": "Model capability unavailable; inspect model configuration"}, status_code=503)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": "Invalid input or unavailable tool: " + str(error)[:200]}, status_code=400)

    @app.exception_handler(OSError)
    async def file_error(request, error):
        return JSONResponse({"detail": "File or local capability unavailable; verify its path and permissions"},
                            status_code=400)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def status():
        inference = await model.status()
        def available(value, reason=""):
            return {"available": bool(value), "reason": reason, "live_verified": False}
        return {"version": __version__, "model": inference, "workspace": str(settings.workspace),
                "vault": {"roots": [str(p) for p in settings.vault_roots]}, "metrics": store.metrics(),
                "capabilities": {
                    "files": available(True), "vault": available(bool(settings.vault_roots), "Configure vault_roots"),
                    "calculator": available(True), "agent": available(inference.get("available", False)),
                    "vision": available(settings.enable_vision and inference.get("available", False),
                                        "Requires explicit enable_vision and compatible vision model"),
                    "browser": available(settings.enable_browser and "offline_render" in settings.allowed_tools,
                                         "Offline screenshots only; enable and authorize offline_render"),
                    "voice": {
                        "available": bool(os.environ.get("JARVIS_WHISPER_BIN") and os.environ.get("JARVIS_WHISPER_MODEL"))
                                     or bool(os.environ.get("JARVIS_PIPER_BIN") and os.environ.get("JARVIS_PIPER_MODEL")),
                        "transcription": bool(os.environ.get("JARVIS_WHISPER_BIN") and os.environ.get("JARVIS_WHISPER_MODEL")),
                        "synthesis": bool(os.environ.get("JARVIS_PIPER_BIN") and os.environ.get("JARVIS_PIPER_MODEL")),
                        "reason": "Configure local voice engines and authorize transcribe/synthesize", "live_verified": False},
                    "images": available(bool(os.environ.get("JARVIS_IMAGE_BIN")), "Configure local image wrapper"),
                    "video": available(shutil.which("ffmpeg")),
                    "telegram": available(settings.telegram_chat_id and os.environ.get("TELEGRAM_BOT_TOKEN")),
                    "telephony": available(settings.phone_number and os.environ.get("TWILIO_AUTH_TOKEN")),
                    "whatsapp": available(os.environ.get("WHATSAPP_ACCESS_TOKEN")),
                    "social": available(os.environ.get("BLUESKY_HANDLE") and os.environ.get("BLUESKY_APP_PASSWORD")),
                    "cloud": available(settings.allow_cloud, "Explicit opt-in required"),
                }, "warnings": ["Model inference is not a guarantee of factual correctness.",
                                 "External adapters need owner credentials and standing permissions."]}

    @app.post("/api/chat")
    async def chat(body: ChatBody):
        return await core.chat(body.message.strip(), body.idempotency_key)

    @app.post("/api/tools")
    async def tool(body: ToolBody):
        return await run_in_threadpool(jobs.run, body.tool, body.arguments, body.idempotency_key,
                                      lambda: tools.execute(body.tool, body.arguments))

    @app.post("/api/vault/index")
    async def index():
        result = await run_in_threadpool(vault.index)
        store.audit("vault_index", result)
        return result

    @app.get("/api/vault/search")
    async def search(q: str = Query(min_length=1, max_length=500), limit: int = Query(default=5, ge=1, le=20)):
        return {"results": await run_in_threadpool(vault.search, q, limit)}

    @app.get("/api/vault/graph")
    async def graph():
        return await run_in_threadpool(vault.graph)

    @app.get("/api/audit")
    def audit():
        return {"events": store.events()}

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": jobs.list()}

    @app.get("/api/workflows")
    def list_workflows():
        return {"workflows": workflows.list()}

    @app.post("/api/workflows")
    def create_workflow(body: WorkflowBody):
        return {"workflow": workflows.create(**body.model_dump())}

    @app.post("/api/workflows/{workflow_id}/pause")
    def pause_workflow(workflow_id: str, body: PauseBody):
        return workflows.pause(workflow_id, body.paused)

    @app.get("/api/focus")
    def get_focus():
        return focus.status()

    @app.post("/api/focus")
    def update_focus(body: FocusBody):
        return focus.action(**body.model_dump())

    @app.post("/api/decisions")
    def decisions(body: RankBody):
        return {"ranked": rank_options(body.options), "note": "Estimates from your inputs, not predictions"}

    @app.post("/api/vision")
    async def vision(body: VisionBody):
        tools.broker.authorize("vision")
        if model.remote:
            raise Denied("private images require local inference")
        if not settings.enable_vision:
            raise Denied("vision requires enable_vision=true and a compatible local model")
        result = await model.chat([{"role": "user", "content": body.prompt}], images=[body.image])
        return {"answer": result["content"], "verified": False, "usage": result["usage"]}

    def local_voice():
        from .extensions.voice import LocalVoice
        return LocalVoice(whisper_executable=os.environ.get("JARVIS_WHISPER_BIN"),
                          whisper_model=os.environ.get("JARVIS_WHISPER_MODEL"),
                          piper_executable=os.environ.get("JARVIS_PIPER_BIN"),
                          piper_model=os.environ.get("JARVIS_PIPER_MODEL"))

    @app.post("/api/voice/chat")
    async def voice_chat(body: VoiceBody):
        tools.broker.authorize("transcribe")
        try:
            audio = base64.b64decode(body.audio, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("voice input must be base64 PCM WAV") from exc
        if len(audio) > 3_000_000 or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
            raise ValueError("voice input must be bounded PCM WAV")
        with tempfile.TemporaryDirectory(prefix="jarvis-voice-request-") as directory:
            source = Path(directory) / "input.wav"
            source.write_bytes(audio)
            transcript = await run_in_threadpool(local_voice().transcribe, source)
        text = transcript["text"]
        if not text.strip() or len(text) > 6000:
            raise ValueError("transcription empty or exceeds request limit")
        if text.startswith("/do "):
            raise Denied("computer delegation requires an explicit text request")
        answer = await core.chat(text)
        return {**answer, "transcript": text}

    @app.post("/api/voice/speak")
    async def voice_speak(body: SpeakBody):
        tools.broker.authorize("synthesize")
        with tempfile.TemporaryDirectory(prefix="jarvis-voice-response-") as directory:
            destination = Path(directory) / "speech.wav"
            await run_in_threadpool(local_voice().synthesize, body.text, destination)
            if destination.stat().st_size > 5_000_000:
                raise ValueError("voice output exceeds byte limit")
            encoded = base64.b64encode(destination.read_bytes()).decode("ascii")
        return {"audio": encoded, "mime": "audio/wav", "verified": "engine_artifact"}

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="ui")
    return app
