# Run / use / verify

## Local startup
Supported baseline: Linux, macOS POSIX runtime, Windows through WSL2. Linux was tested here; native Windows desktop control is not implemented.

```sh
uv sync --frozen --extra documents
uv run --frozen jarvis init
uv run --frozen jarvis serve
```

Configuration is `.jarvis/config.toml`; state and a dedicated writable workspace live beneath `.jarvis/`. Initialization preserves existing configuration. `uv run jarvis token` explicitly displays the owner token locally; paste it into the web interface's connection form. Never paste it into chat, URLs, shared logs, or Git. The default service listens only on the local loopback interface, port 8787. Web tokens remain in tab memory and are cleared on disconnect.

Useful local commands:
```sh
uv run jarvis doctor
uv run jarvis index
uv run jarvis ask '/calc (120 * .8) - 30'
uv run jarvis ask '/find cancellation'
uv run jarvis backup --output /path/outside-state/jarvis-backup.zip
uv run jarvis restore --archive /path/jarvis-backup.zip --directory /path/new-jarvis
```
Restored configuration is `new-jarvis/jarvis.toml`; use `--config` on later commands. Backups include private source data: protect them. Credentials are excluded and tokens are regenerated; restored recurring workflows are paused. See PORTABILITY.md.

## Core workflow
Write or copy non-secret text/Markdown/CSV into the dedicated workspace, then index. The UI searches sources, shows a literal-link graph, and runs scoped tools with receipts. `/find QUERY` returns exact excerpts; `/calc EXPRESSION` uses bounded Decimal arithmetic. Ordinary conversation and `/do GOAL` need a running compatible model. `/do` validates all proposed steps before dispatch and cannot invoke arbitrary shell/network/admin tools.

Configure additional `vault_roots` only for directories you intend to expose. Reconciliation runs every `auto_index_seconds` (default 60); `0` disables automatic indexing. PDF is an optional dependency and extraction runs with resource limits; this is not a full OS security sandbox. Secret directories/files are excluded by shared policy.

Recurring workflows can be configured through Workspace tools or the authenticated workflows API. An interval is 60 seconds..30 days. They execute only permitted tools. Failures pause the workflow; inspect jobs/audit before resuming. External provider keys are unique per scheduled occurrence and stable for that occurrence. Restarts do not retry uncertain effects. This scheduler skips missed ticks rather than replaying a backlog.

Recurring `notify` jobs wait during configured local quiet hours or an active focus session. Set your IANA `timezone`; equal `quiet_start`/`quiet_end` disables the quiet window. Notifications check again after a minute and retain one pending occurrence, without a backlog. An explicit owner-configured `urgent: true` notification bypasses this deferral; daily caps still apply. Immediate owner tool requests are not background nudges and execute immediately. Telegram polling persists a hashed account-bound update cursor before processing, preventing duplicate replies after restart. An interrupted/uncertain reply is not resent automatically.

## Local models
Install a verified Ollama or llama.cpp runtime and an appropriately licensed hardware-sized model yourself using the official distribution. Default adapter: Ollama's local OpenAI-compatible endpoint and `qwen3:8b`. This is a compatibility baseline, not a current leaderboard claim. Exact artifact licenses/checksums and actual performance require evaluation; see RESEARCH.md/MODELS.md. No model weights were installed here because required registries returned proxy CONNECT 403.

For llama.cpp, set `model_url` to its local `/v1` endpoint and `model_name` to its configured alias. A remote endpoint requires HTTPS and `allow_cloud=true`; optional API credentials come from the named environment variable. Private vault context, images, and computer planning do not go through generic cloud inference. Codex subscription integration, if configured, uses its official CLI rather than token reuse (CODEX.md).

Enable vision only after selecting a compatible local vision model (`enable_vision=true`); call authenticated `/api/vision` with base64 PNG/JPEG/WebP. Inference remains unverified text.

## Optional packs and credentials
Core needs no API keys. Optional outbound packs require owner account setup, official platform permissions, configuration destinations, budgets, and tool names added to `allowed_tools`:

| Feature | Configuration / binding names | Tools |
|---|---|---|
| Telegram | `telegram_chat_id`, optional `telegram_user_id`; `TELEGRAM_BOT_TOKEN` | `notify` |
| Phone | `phone_number`, `twilio_from_number`, call/message caps; `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | `notify` |
| WhatsApp | `phone_number`, `whatsapp_phone_id`; `WHATSAPP_ACCESS_TOKEN` | `notify` |
| Bluesky | daily cap, forbidden terms; `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | `social_publish`, `social_pause` |
| Local voice | `JARVIS_WHISPER_BIN`, `JARVIS_WHISPER_MODEL`, `JARVIS_PIPER_BIN`, `JARVIS_PIPER_MODEL` | `transcribe`, `synthesize` |
| Images | owner-approved `JARVIS_IMAGE_BIN` wrapper + local licensed weights | `generate_image` |
| Offline screenshots | Playwright/system Chromium; `enable_browser=true` | `offline_render` |
| Isolated terminal | Docker daemon + locally installed digest-pinned `sandbox_image` | `sandbox_run` |

Set secrets through your OS credential manager/service environment, never tracked TOML. Credential presence is only configuration evidence. This build exercised protocol contracts, not owner accounts. Provider receipts mean accepted submission, not final delivery. Notification/fallback journals preserve uncertain outcomes and enforce daily caps. Do not delete journals to retry failed effects. Bluesky is the supported social platform; this is not universal autonomous social growth/engagement.

Voice UI accepts 16 kHz mono PCM WAV up to 30 seconds. This release has WAV voice interaction and local playback, not a wake-word or full duplex conversation. Optional engines require their own licenses/models; no browser/cloud speech recognition substitution.

Run the read-only owner Telegram interface separately: `uv run jarvis telegram`; it ignores unauthorized senders and does not accept computer delegation through messaging. It is text-only, and no inbound WhatsApp webhook is exposed.

## Remote reach
Use an owner-controlled WireGuard/Tailscale network, configure the exact private hostname/IP in `allowed_hosts`, and bind to the private interface with `jarvis serve --host PRIVATE_IP`. Keep application bearer authentication. Use trusted TLS if browser features require a secure origin. Do not forward the service publicly. The host must be awake and connected. A private network setup has not been exercised on the owner's devices here.

## Containers
Optional recipe; actual Docker build/run was not possible here (daemon/image unavailable).
```sh
docker compose build
docker compose run --rm jarvis python -m jarvis.cli init --directory /data
docker compose up -d
```
Compose binds host loopback only and retains `/data`. Do not mount the Docker socket into the service container. A container-local loopback model must be supplied separately; native deployment is the straightforward choice for a host Ollama/desktop adapter. Pin the base image by an inspected digest for a production release. Browser/voice models are not bundled in the core image.

## Verification
```sh
sh scripts/check.sh
```
Runs frozen installation, lint, meaningful tests, an actual disposable HTTP-service smoke, and wheel build. Browser tests use system Chromium when available; FFmpeg tests perform actual rendering when installed. Missing optional runtimes produce explicit skips, not passing capability claims. No real external account is contacted. `doctor` inspects names/status only and does not print credentials. One dependency deprecation warning is documented in STATUS.md.

## Recovery and data protection
Start one serving process per state directory. Active jobs interrupted by restart become uncertain and are not resent. Background reconciliation rebuilds changed derived records; workflow failures pause rather than loop. Recovery never expands privileges or changes policies. SQLite is not encrypted; use full-disk encryption, private state permissions, and protected backups. Same-account malware and malicious owner-configured executables are outside the broker's threat boundary. Screen/terminal packs are narrowly scoped; full native desktop automation and continuous camera capture are not shipped.
