# Architecture decision register

Accepted 2026-10-10. These decisions supersede unverified claims in the supplied brief. Revisit when measurements justify a change.

| ADR | Decision / why | Alternatives / reconsider when |
|---|---|---|
| 001 Small core | Python, FastAPI, httpx, SQLite, plain browser assets: few services, easy backup and isolated tests. | Swarms, Redis and Kubernetes add unnecessary cost. Evaluate OpenJarvis after exact license/version and measured task benefit. |
| 002 File-backed vault | Authorized folders stay canonical; SQLite FTS and literal-link graph are rebuildable. Hashes reconcile changes; edges carry evidence. | Obsidian is a proprietary viewer; Neo4j adds a service; unknown Graphify/Memoria identities are not dependencies. Add embeddings only after measured retrieval gaps. |
| 003 Model neutrality | Local compatible protocol; Qwen3 8B is a compatibility baseline. Newer candidates need exact weight licenses, checksums and hardware measurements. | No assumed lightweight giant MoE; no unsourced benchmark rankings. Record quantization/revision before installing. |
| 004 Separate authority | Model proposes bounded typed steps; broker decides. Scoped descriptors and staged outputs; no arbitrary host shell. | Model-managed permissions are insufficient. Wider control requires OS-specific adapters and sandbox tests. |
| 005 Honest verification | Deterministic calculations, provenance and result checks; model suggestions remain unverified. ROI ranks supplied estimates. | A second model cannot guarantee truth. No fabricated probabilities or hidden chain-of-thought logs. |
| 006 Effect recovery | Persist keys before dispatch; journal effects before sending; uncertainty blocks retries; failures pause schedules. | Blind retries can duplicate effects. Reconcile provider receipts before another uncertain action. |
| 007 Subscription boundary | Explicit official Codex CLI suggestions with separate owner login; no vault/chat context attached. | Generic ChatGPT-plan OAuth entitlement unestablished; no extracted tokens. API access needs separately authorized credentials. |
| 008 Official channels | Telegram, WhatsApp Business, Twilio, Bluesky with owner bindings, caps, pause and receipts. | Cookie automation and detection evasion are fragile. Add networks through authorized APIs. |
| 009 Private reach | Loopback, random private token, exact host allowlist; private VPN for remote devices. | Public forwarding unnecessary. Multi-owner identity is separate work. |
| 010 Optional packs | FFmpeg, whisper.cpp/Piper/image wrapper and digest-pinned Docker worker are explicit extras. | No mandatory weights/browser/daemon/GPU. Runtime and weight licenses need separate checks. |
| 011 ADHD support | One focus task, pause/resume, small starting action, completed outcomes and calm interface. Recurring nonurgent notifications queue during quiet hours or active focus; owner-configured urgency can bypass. | Continuous body doubling and adaptive nudges need owner-device tests. Immediate owner tool requests remain immediate; scheduler deferral does not suspend other business work. |
| 012 Conservative restore | Online database snapshots, filtered checksummed archive, staged restore, new token, stripped external roots, paused workflows. | Blind copying can capture inconsistent databases or resurrect actions. Second-directory tests do not establish second-machine portability. |

Versions are pinned in `uv.lock` and hash-pinned deployment requirements. External runtime artifacts are separately configured. No application license is assigned on the repository owner's behalf; redistribution needs an explicit owner license decision.
