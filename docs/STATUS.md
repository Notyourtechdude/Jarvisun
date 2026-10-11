# Build ledger — 2026-10-11 (owner timezone)

Baseline: README-only repository. PRD, architecture and ordered build plan were saved before code. Owner authorized the entire build without further approval. Existing cloud checkout used; no public deployment, account connection or repository push.

**Release verification:** 217 tests passed, none skipped; one upstream AnyIO/Starlette deprecation warning. Ruff and real disposable HTTP smoke passed; source distribution and wheel built. Real local tests cover persistence, resource-bounded document extraction, Chromium/interface, offline 3D interaction, FFmpeg and restore. External API/model transports and Codex protocol executables are contract fixtures, not live provider checks.

| Block | Shipped / use | Verification / limits |
|---|---|---|
| 0 Docs | PRD, architecture, build plan, decision register, primary-source research | Decisions and dependency graph; unverified claims rejected. |
| 1 Foundation | `jarvis init`, `doctor`, `serve`; private versioned config | Foundation/CLI tests; initialized local instance. No GPU or installed weights. |
| 2 Broker/audit | Scoped paths, private-file denials, descriptor-based access, authenticated API, redacted SQLite receipts | Security/API/regression tests for traversal, parent swaps, hardlinks and private state. Same-owner malware outside scope. |
| 3 Brain | Swappable bounded local HTTP adapter; `/do` plans up to four permitted steps | 29 model contract tests and preflight tests. Actual model load/performance **blocked** by registry egress; newer exact weight licenses unchecked. |
| 4 Vault | Incremental configured-folder text/Markdown/CSV/PDF index, FTS search, source-backed wikilink graph | Real updates/deletions, malformed PDFs and worker limits. `jarvis index`, `/find`, UI. No whole-computer semantic entity extraction. |
| 5 Quality/returns | Decimal arithmetic, exact excerpts, checked tool results, unverified model outputs, net-return estimates | Core tests; `/calc`, `/api/decisions`. Checks computation/provenance/execution, not universal truth. |
| 6 Hands | Atomic scoped files, staged artifacts, offline screenshots, pinned container adapter | Real files/screenshots; container contracts. Docker daemon/image unavailable. Native apps, live browser sessions, camera, mouse/keyboard **not implemented**. |
| 7 Reach | Responsive owner interface: sources, graph, tools, jobs, audit, workflows | 16 Chromium frontend tests including real-server flows. Owner-device VPN/TLS/rebinding not exercised. See INTERFACE/OPERATIONS. |
| 8 Voice/focus | Timer/pause/resume/completion; WAV transcription/speech adapters; background nonurgent notifications defer during quiet hours/active focus | Focus/voice/API/UI and scheduler tests. Owner urgency bypasses deferral, not caps. No speech weights, continuous microphone/wake word, adaptive nudges or activity-sensing body doubling. |
| 9 Notifications | Owner-only Telegram with persistent account-bound update cursor, official WhatsApp outbound, capped Twilio calls/SMS and journaled fallback | Channel/policy/duplicate/restart/concurrency and cursor-backup tests. Credentials/numbers absent; no live calls. Interrupted replies are not automatically resent. |
| 10 Frontier | Explicit stdin-only `jarvis frontier --enable`, official Codex CLI, separate auth home | 15 provider subprocess tests plus CLI integration. No live login/inference; no general subscription OAuth or automatic private routing. See CODEX.md. |
| 11 Recovery | Keyed durable jobs, uncertain-state recovery, recurring workflows, failure pause and index reconciliation | Core/workflow/regression tests. No privilege expansion or general software-patching self-healer; uncertain effects require reconciliation. |
| 12 Business | Exact CSV totals/margins, ranked net-return inputs, recurring tools | Fixture calculations and malformed inputs. Owner data/currency needed; not accounting integration or a full business operator. |
| 13 Creative | Self-contained interactive projected 3D business page, optional CDN Three.js enhancement, real silent FFmpeg cuts/captions, local image wrapper | Real Chromium pixels/drag/keyboard/pause/reset/reduced-motion/mobile/zero-default-egress tests and MP4 checks. Template artifact, not bespoke site generation. No image weights or diffusion video generation. |
| 14 Social | Official Bluesky text publishing, caps, terms, pause, effect journal and recurring workflows | Mocked API contracts. No connected account; analytics/engagement/strategy/other networks unimplemented. |
| 15 Release | Frozen/hash-pinned dependencies, packages, smoke/check scripts, Docker/Compose, CI, backup/restore | Full checks; second-directory restore. Docker/CI and second-machine portability not run; drivers/secrets need rebinding. |

## Use and evidence

`sh scripts/check.sh` reproduces release checks. `jarvis doctor` reports unavailable model and missing account credentials without exposing values. OPERATIONS.md contains use/verify notes and optional setup. No account effects, model benchmarks, business returns or cross-device readiness are inferred from mocks. The broader ambition exceeds this verified release; explicit gaps above are remaining engineering or owner/runtime prerequisites.

The latest wheel was installed into a fresh virtual environment outside the checkout using hash-pinned runtime requirements; real HTTP smoke passed with packaged static assets and restart-recovery module present. The configured local service was restarted with final code on loopback port 8787; health, authentication denial/acceptance and calculation were checked without logging its token. Fresh-machine/cloud-task restoration remains untested.

## Cloud setup persistence

Configuration backend confirmed a saved draft with tested dependency/init/smoke setup and startup instructions. Custom domains added: `registry.ollama.ai`, `ollama.com`, `huggingface.co`, `cdn.playwright.dev`, `developers.openai.com`; preset and repository membership preserved. Saving does not apply/publish current network or environment changes. Review and save the draft in environment settings, then publish to activate it. Downstream artifact CDN destinations may still need discovery; weight downloads/fresh-task restoration are not established.
