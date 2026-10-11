# Inference configuration

Jarvis uses one bounded OpenAI-compatible adapter, `LocalModel`. Change `model_url` and `model_name` to swap a locally served model; no orchestration code depends on a particular model family. Supported protocol paths are `GET /models` and `POST /chat/completions`, under the configured base URL. Ollama, llama.cpp, and vLLM expose this protocol; individual model/template compatibility still requires a live check.

Default configuration:

```toml
model_url = "http://127.0.0.1:11434/v1"
model_name = "qwen3:8b"
max_tokens = 512
request_timeout = 60
allow_cloud = false
```

`qwen3:8b` is a reproducible text baseline, not a claim that this is the newest or best model. See [RESEARCH.md](RESEARCH.md) for dated candidates, evidence, and license limitations. Jarvis installation never downloads model weights. On a machine with Ollama and suitable free memory, prepare the model separately with `ollama pull qwen3:8b`, then keep `ollama serve` running. This downloads several GB and is an owner setup step. Its default port is 11434. With llama.cpp, load a separately acquired compatible GGUF file, bind the server to loopback, set `--alias` to the configured `model_name`, and use the server's `/v1` base URL. For vision, load a compatible vision model and its required projector; text weights alone do not add vision.

Cloud inference requires `allow_cloud = true`, an HTTPS endpoint, and a credential in the variable named by `model_key_env` (default `JARVIS_MODEL_API_KEY`). This binding can also authenticate a local server. Only that configured binding is read; Jarvis does not inspect Codex, ChatGPT, browser, or other applications' credential files. Put the key in secure environment configuration, never in TOML or chat. HTTPS outside loopback is treated as remote even for a private LAN server. Environment proxy routing and redirects are disabled for inference; provider access must work through the configured route. Jarvis web sign-in, provider API authentication, and Codex's own sign-in are separate concerns.

`status()` reports available only when the server lists the configured exact model ID. Listing proves service/model discovery, not successful generation, loaded weights, speed, or vision support. A live readiness check additionally needs one successful `chat()` call. Missing service/weights, HTTP failures, redirects, malformed data, and exceeded budgets produce explicit unavailable results; there is no fake generated reply. A valid chat receipt includes `content`, configured `model`, numeric token `usage` when supplied by the backend, `elapsed_ms`, and `verified: false`. Model text is evidence to assess, not proof that a tool action happened.

Bounds are 64 messages, 32,000 UTF-8 content bytes, configured `max_tokens` (16–8,192), and an additional output limit of `max_tokens * 16` UTF-8 bytes. Oversized input/output is rejected, not silently truncated. The byte budget is not an exact token count; configure the inference server's context window independently. Server-reported completion counts above the token budget are rejected. The HTTP body is bounded at 2 MB; status metadata is bounded at 64 KB. Each HTTP operation uses the configured timeout and does not retry automatically. Some reasoning models may consume the small token budget before producing text; use a non-thinking/instruct variant or an appropriate budget. Provider-specific Responses APIs, streaming, model tool calls, automatic model selection, and Codex subprocess integration are not implemented.

`chat(messages, images=[...])` accepts raw base64 or `data:image/...;base64,...` for PNG, JPEG, or WebP, attaches images to the final user message, and rejects external image URLs. Limits: four images, 5 MB per decoded image, 10 MB decoded total. Encoding, signatures, declared MIME, and size are checked before network access; these checks do not fully decode an image or prove vision-model support. The server must enforce its own pixel/decoder limits. Earlier messages are plain strings with `system`, `user`, or `assistant` roles. Caller history is not mutated.

HTTP contract tests use `httpx.MockTransport` and require no weights or external network. They verify request shape, exact model discovery, timeouts, network restrictions, redacted failures, image/context/output budgets, and unverified receipts. They do not establish local model quality, latency, memory use, or cloud credentials working in production.

## Real CPU inference readiness check, 2026-10-10

This cloud container has a four-core CPU quota (five visible CPUs), a 32 GiB cgroup memory limit, and no detected GPU or installed Ollama/llama.cpp runtime. A small quantized `qwen3:0.6b` was considered for a real smoke check within a 2 GB weight-download budget. No weights were downloaded and no model endpoint was started.

The exact official Ollama model-manifest request, `https://registry.ollama.ai/v2/library/qwen3/manifests/0.6b`, failed at the HTTPS proxy with CONNECT `403 Forbidden`. `ollama.com` downloads and the `huggingface.co` official Qwen3-0.6B-GGUF API lookup also failed with the same proxy denial. This is a network prerequisite, not evidence of insufficient CPU/RAM or a model error. No unchanged denied requests were retried, network policy bypass attempted, or TLS/checksum verification disabled.

Official GitHub release metadata, the SHA-256 checksum file, and a HEAD request for Ollama v0.40.2's Linux AMD64 archive were accessible via `github.com` → `release-assets.githubusercontent.com`. The archive was 1,436,368,794 bytes and its publisher SHA-256 was `726bee78706c281b0eeef00746efe51a044d71c592c3f0b195820707f31fdf04`; the archive was not downloaded because the weights remained inaccessible. Accessible runtime distribution alone does not establish inference readiness.

Required initial destinations for the Ollama route are `registry.ollama.ai` (manifest/blobs) and `ollama.com` (standard official installer download), or the already reachable official GitHub release route. A Hugging Face alternative needs `huggingface.co` and any exact artifact-storage hosts its model download redirects to; those cannot be determined from the blocked lookup here. Additional blob/CDN destinations must be recorded from a permitted manifest/download response rather than guessed. Readiness evidence, without secrets, is saved outside the repository at `/workspace/.jarvis-model-runtime/readiness.json`. The application must continue reporting model inference unavailable until a real generation check succeeds.
