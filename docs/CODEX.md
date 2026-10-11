# Optional owner-authenticated Codex coding suggestions

This provider is disabled by default. It wraps the **official Codex CLI**, using an explicitly supplied executable and a separate owner-controlled authentication directory. It provides coding suggestions through Codex's supported login/inference flow; it is not a general API for arbitrary AI subscriptions. Jarvis never extracts subscription tokens, reads credential files, reuses browser cookies, or discovers this cloud harness's Codex authentication. No live Codex login or inference was exercised during implementation.

## Owner configuration and supported use

The caller must supply all three: explicit `enabled=True`, an absolute owner-approved Codex executable, and an existing absolute auth-home directory owned by the current user with no group/other permission bits (normally mode 0700). Auth-home symlinks, harness authentication locations, and the inherited `CODEX_HOME` location or its aliases/ancestors/descendants are denied. Protected locations include `/workspace/.codex`, `/root/.codex`, `/home/agent/.codex`, and the inherited home directory's `.codex`.

The owner performs the official login **manually**, in their own shell, using their own executable and the same independent authentication directory. For example, after creating a private directory outside repository/vault roots:

```sh
CODEX_HOME=/absolute/owner/private-codex-auth /absolute/owner/codex login
```

Do not use this harness's home, login files, token values, or credentials. Follow the current official Codex authentication documentation for browser/device login and eligible plans. A private auth home should contain login artifacts only; implicit `AGENTS.md`, `AGENTS.override.md`, skills, and hooks are rejected. Jarvis does not run login, inspect whether login artifacts exist, or accept secret values in its own configuration. Authentication storage and refresh remain the official CLI's responsibility.

```python
provider = CodexProvider(workspace, owner_executable, owner_auth_home, enabled=True)
status = provider.available()
result = provider.run("Suggest a small Python implementation for this public task.")
```

The shipped local CLI accepts an explicitly supplied prompt on stdin:

```sh
uv run --frozen jarvis frontier --enable \
  --executable /absolute/owner/codex --auth-home /absolute/owner/private-codex-auth < public-task.txt
```

Without `--enable` it returns a disabled receipt. It never attaches vault or chat context. This CLI is deliberately separate from web chat and automatic routing.

`available()` means explicit owner configuration is present and the executable/private directory exist. Its `authenticated: "not_checked"` field makes clear this is **not** a live provider connection or valid-subscription claim. The service should expose this provider through a separate explicit owner-requested frontier route, never automatically route vault/private conversations to it. Prompts are intentionally supplied by the owner/caller; they are sent to the external Codex provider when execution is enabled. The `workspace` argument preserves the caller's integration scope, but its files are not read, copied, mounted, or appended to the prompt.

The wrapper uses a new empty mode-0700 temporary task directory outside the repository, with that directory as process CWD, Codex `--cd`, and `HOME`. It invokes argv with `shell=False`:

```text
owner-codex exec --sandbox read-only --json --skip-git-repo-check
  --ephemeral --ignore-user-config --ignore-rules
  -c features.shell_tool=false -c features.unified_exec=false
  --cd empty-task-directory -
```

The final `-` supplies the bounded prompt through stdin, not command-line arguments. `--ignore-user-config` leaves authentication using the explicit `CODEX_HOME` while suppressing its user configuration; rules and persisted session files are disabled too. The official shell/unified-exec feature flags are disabled to narrow suggestions. Only `PATH`, `LANG`, task-local `HOME`, and explicit `CODEX_HOME` are passed to the child. Host `OPENAI_API_KEY`, harness authentication variables, proxy settings, and application secrets are not inherited. Unsupported CLI flags/protocol versions fail explicitly rather than relaxing these options.

**This is a trusted CLI/provider boundary, not Jarvis broker enforcement or OS secret isolation.** Codex's read-only sandbox restricts writes; it does not by itself hide all host files. Empty CWD, absent implicit repository context, and disabled shell tools do not establish a secret-masking filesystem sandbox. Do not enable this adapter on a sensitive host unless the owner accepts the trusted official CLI boundary or provides independently enforced OS isolation. Do not grant it access to private files by prompt. An explicit opt-in does not make model text a verified action result.

## Bounds and receipts

Prompts are limited to 16,000 UTF-8 bytes. Executions are limited to 60 seconds and 128 KB stdout; subprocess stderr is suppressed. Timeout/overflow terminates the process group, and the empty task directory is removed. Only official JSONL event types are accepted. The final `item.completed` with `item.type == "agent_message"` is returned only with a `turn.completed` event and zero process exit status. Error/failed/malformed/unsupported/incomplete streams produce safe failures without raw backend error output, reasoning summaries, or command logs.

A successful suggestion returns `completed: true`, `verified: false`, `provider: "codex"`, `content`, selected numeric `usage`, and elapsed time. Failures return `completed: false`, `verified: false`, empty content, and a safe reason. Completion confirms a returned suggestion, not a tool action, file change, private-data isolation, or business outcome. Async FastAPI callers should invoke `await run_in_threadpool(provider.run, prompt)`; the adapter's interface is synchronous and bounded.

## Official source verification, 2026-10-10

Live HTTPS reads of the following official GitHub files confirmed the actual flags/protocol. No real Codex binary, login, or provider request was executed for these checks.

- [Exec CLI declarations](https://github.com/openai/codex/blob/main/codex-rs/exec/src/cli.rs): stdin `-`, `--json`, `--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, and `--ignore-rules`.
- [Shared CLI options](https://github.com/openai/codex/blob/main/codex-rs/utils/cli/src/shared_options.rs): `--sandbox` and `--cd`.
- [Official feature registry](https://github.com/openai/codex/blob/main/codex-rs/features/src/lib.rs): `shell_tool` and `unified_exec` configuration keys.
- [Exec JSONL event definitions](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs): `item.completed`, `agent_message` text, `turn.completed` usage, errors, and failed turns.
- [Official Codex README](https://github.com/openai/codex/blob/main/README.md#using-codex-with-your-chatgpt-plan): supported ChatGPT-plan sign-in and separate API-key authentication. [Detailed auth documentation](https://developers.openai.com/codex/auth) was blocked by this environment's HTTPS proxy, as recorded in RESEARCH.md.

Tests run controlled temporary executable fixtures emitting the documented protocol; they verify argv, stdin, fresh empty task CWD, credential-environment isolation, private auth-home exclusions, failures, output limits, timeout termination, and unverified suggestions. These are real subprocess contract checks, not a claim of live subscription authentication.
