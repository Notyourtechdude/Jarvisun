"""Bounded OpenAI-compatible inference. Model text is always unverified input."""
import base64
import binascii
import json
import os
from time import monotonic
from urllib.parse import urlparse

import httpx

from jarvis.config import Settings

MAX_MESSAGES = 64
MAX_CONTEXT_BYTES = 32_000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5_000_000
MAX_TOTAL_IMAGE_BYTES = 10_000_000
MAX_RESPONSE_BYTES = 2_000_000


class ModelUnavailable(RuntimeError):
    """Safe public failure without backend response bodies or credential details."""


class LocalModel:
    """Works with Ollama, llama.cpp, vLLM, and opted-in compatible HTTPS APIs.

    Text/context bounds are byte budgets, not tokenizer-exact estimates. Servers
    must configure their own context limit; max_tokens also bounds generation.
    A transport may be supplied for isolated HTTP contract tests.
    """

    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport
        parsed = urlparse(settings.model_url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (not local and (not settings.allow_cloud or parsed.scheme != "https"))):
            raise ModelUnavailable("Model endpoint configuration is not permitted")
        if not isinstance(settings.model_name, str) or not 1 <= len(settings.model_name) <= 256:
            raise ModelUnavailable("Model name configuration is invalid")
        self.remote = not local
        self.base_url = settings.model_url.rstrip("/")

    def _client_options(self):
        return {"timeout": self.settings.request_timeout, "follow_redirects": False,
                "trust_env": False, "transport": self.transport}

    def _headers(self):
        headers = {"Accept": "application/json"}
        # Only this explicitly configured binding is read. No login/session files.
        key = os.environ.get(self.settings.model_key_env)
        if self.remote and not key:
            raise ModelUnavailable("Configured cloud model credential is unavailable")
        if key:
            if "\r" in key or "\n" in key:
                raise ModelUnavailable("Configured model credential is invalid")
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def _request(self, method, path, payload=None, limit=MAX_RESPONSE_BYTES):
        try:
            async with (httpx.AsyncClient(**self._client_options()) as client,
                        client.stream(method, self.base_url + path, headers=self._headers(),
                                      json=payload) as response):
                if not 200 <= response.status_code < 300:
                    raise ModelUnavailable(f"Model service unavailable (HTTP {response.status_code})")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > limit:
                        raise ModelUnavailable("Model response exceeds the byte budget")
                    body.extend(chunk)
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ModelUnavailable("Model service returned a malformed response")
            return data
        except ModelUnavailable:
            raise
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise ModelUnavailable("Model service unavailable or returned malformed data") from None

    def _messages(self, messages, images):
        if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
            raise ModelUnavailable("Conversation exceeds the message budget or is empty")
        clean = []
        size = 0
        for message in messages:
            if not isinstance(message, dict):
                raise ModelUnavailable("Conversation message is invalid")
            role, content = message.get("role"), message.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ModelUnavailable("Conversation roles and content must be plain text")
            try:
                size += len(content.encode("utf-8"))
            except UnicodeEncodeError:
                raise ModelUnavailable("Conversation contains invalid text") from None
            if size > MAX_CONTEXT_BYTES:
                raise ModelUnavailable("Conversation exceeds the context byte budget")
            clean.append({"role": role, "content": content})
        if images is not None:
            if not isinstance(images, list) or len(images) > MAX_IMAGES:
                raise ModelUnavailable("Images exceed the count budget")
            if images and clean[-1]["role"] != "user":
                raise ModelUnavailable("Images require a final user message")
            total = 0
            parts = [{"type": "text", "text": clean[-1]["content"]}]
            for encoded in images:
                url, length = self._image(encoded)
                total += length
                if total > MAX_TOTAL_IMAGE_BYTES:
                    raise ModelUnavailable("Images exceed the total byte budget")
                parts.append({"type": "image_url", "image_url": {"url": url}})
            if images:
                clean[-1]["content"] = parts
        return clean

    @staticmethod
    def _image(encoded):
        if not isinstance(encoded, str):
            raise ModelUnavailable("Image must be base64 text")
        declared = None
        if encoded.startswith("data:"):
            header, separator, encoded = encoded.partition(",")
            if not separator or header not in {"data:image/png;base64", "data:image/jpeg;base64",
                                               "data:image/webp;base64"}:
                raise ModelUnavailable("Image data format is unsupported")
            declared = header.split(";")[0][5:]
        if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise ModelUnavailable("Image exceeds the byte budget")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ModelUnavailable("Image base64 is invalid") from None
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ModelUnavailable("Image is empty or exceeds the byte budget")
        mime = None
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif raw.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            mime = "image/webp"
        if mime is None or (declared is not None and declared != mime):
            raise ModelUnavailable("Image signature does not match a supported format")
        return f"data:{mime};base64,{encoded}", len(raw)

    async def chat(self, messages: list[dict], images: list[str] | None = None) -> dict:
        clean = self._messages(messages, images)
        started = monotonic()
        data = await self._request("POST", "/chat/completions", {
            "model": self.settings.model_name, "messages": clean,
            "max_tokens": self.settings.max_tokens, "stream": False,
        })
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ModelUnavailable("Model service returned malformed chat data") from None
        if not isinstance(content, str) or not content.strip():
            raise ModelUnavailable("Model service returned no text")
        try:
            length = len(content.encode("utf-8"))
        except UnicodeEncodeError:
            raise ModelUnavailable("Model service returned invalid text") from None
        if length > self.settings.max_tokens * 16:
            raise ModelUnavailable("Model text exceeds the output byte budget")
        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        safe_usage = {name: usage[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")
                      if type(usage.get(name)) is int and 0 <= usage[name] <= 1_000_000_000}
        if safe_usage.get("completion_tokens", 0) > self.settings.max_tokens:
            raise ModelUnavailable("Model generation exceeds the output token budget")
        return {"content": content, "model": self.settings.model_name, "usage": safe_usage,
                "elapsed_ms": round((monotonic() - started) * 1000, 2), "verified": False}

    async def status(self) -> dict:
        try:
            data = await self._request("GET", "/models", limit=64_000)
            models = data.get("data")
            if (not isinstance(models, list) or any(not isinstance(entry, dict)
                    or not isinstance(entry.get("id"), str) for entry in models)):
                raise ModelUnavailable("Model service returned malformed model metadata")
            available = any(entry["id"] == self.settings.model_name for entry in models)
            return {"available": available, "model": self.settings.model_name,
                    "reason": "Model listed by service" if available else "Selected model is not listed"}
        except ModelUnavailable as error:
            return {"available": False, "model": self.settings.model_name, "reason": str(error)}
