# Jarvisun — Local Jarvis

A local-first personal operator with an authenticated responsive interface, private incremental vault, scoped computer tools, evidence/calculation checks, focus sessions, durable jobs and recurring workflows.

```sh
uv sync --frozen --extra documents
uv run --frozen jarvis init
uv run --frozen jarvis serve
```

Use `uv run jarvis token` locally to obtain the private owner token for the interface. Configuration and data live in ignored `.jarvis/`; no cloud credential is required for the core. A local model runtime/weights must be configured for conversation and agent planning. Calculations, source retrieval, files, focus and workflows work without inference.

Start with [operations](docs/OPERATIONS.md), [PRD](docs/PRD.md), [architecture](docs/ARCHITECTURE.md), and [build plan](docs/BUILD_PLAN.md). Model/license/authentication evidence is in [research](docs/RESEARCH.md). [Build status](docs/STATUS.md) distinguishes implemented capabilities from live account/hardware prerequisites.

Optional packs: official Telegram/WhatsApp/Twilio/Bluesky adapters, local voice/image wrappers, business CSV analysis, self-contained interactive 3D website artifacts, real FFmpeg cuts/captions, offline screenshots and a digest-pinned isolated container worker. [Codex](docs/CODEX.md) provides explicit owner-authenticated coding suggestions through its official CLI. See [extensions](docs/EXTENSIONS.md) and [computer adapters](docs/COMPUTER.md). These are bounded integrations, not unrestricted desktop control or guaranteed autonomous business growth.

Verify with `sh scripts/check.sh`. All external effects in automated tests are mocked; browser, media, PDF extraction, persistence and restore checks exercise real local behavior. No model benchmark or account connection is claimed without a live check.
