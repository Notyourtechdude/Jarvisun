# Jarvis PRD

Status: implementation baseline, 2026-10-10. Single owner; local-first; all implementation blocks authorized.

## Vision and personas
Private personal operator: understand, decide, act, verify, and maintain momentum. The owner delegates computer and business work; the mobile owner accesses the same machine remotely; the owner experiencing ADHD friction needs a tiny next action, calm body doubling, and fewer interruptions.

## User stories
- Find a document and cite its exact source; distinguish source evidence from interpretation.
- Answer concisely using local models, with honest abstention when evidence or inference is unavailable.
- Perform scoped file, browser, creative, and API tasks; verify resulting state before reporting completion.
- Run recurring business/social workflows under standing policies, budgets, destination restrictions, and an emergency pause.
- Start a focus session, split a stuck task into one achievable next action, resume without guilt.
- Receive owner-only Telegram notifications or a budgeted phone call with fallback.
- Restore data and configuration on a second machine, rebind credentials securely.

## Requirements
Brain: bounded planning, swappable model adapters, token budgets. Eyes: images/documents through optional local vision/OCR adapters. Hands: tools authorized independently of model output. Vault: incremental authorized-folder indexing, full-text search, evidence-backed graph, deletion reconciliation. Reach: authenticated responsive web interface; private VPN preferred. Voice: local optional transcription/synthesis; text always works. Recovery: durable jobs, bounded retries, no automatic privilege expansion. Business/creative/social: independently usable modules and official API adapters, no session-cookie extraction.

## Acceptance measures
Zero unauthorized executions in denial tests. Every action returns a verified receipt or explicit failure. Retrieval answers carry source references; missing knowledge abstains. Calculator uses executable restricted arithmetic. Repeated idempotency keys do not duplicate external effects. Incremental indexing reflects edits/deletes. Authentication failures do not disclose data. Focus mode respects quiet settings. Track token counts, elapsed time, job outcomes, and costs rather than inventing confidence intervals.

## Autonomy and non-goals
Standing policies authorize recurring work; no repeated permission prompts within that policy. Owner setup is still required for paths, devices, credentials, platform publishing rights, and budgets. No guaranteed absence of hallucinations, unlimited self-repair, root access, whole-disk surveillance, medical treatment, detection evasion, or subscription circumvention. Do not claim external integrations are live when only contract tests ran.

## Hardware and availability
Hardware discovery selects models. The service runs without model weights and reports unavailable capabilities honestly. The host must remain awake and connected for remote operation. Creative and voice models are optional packs, not core dependencies.
