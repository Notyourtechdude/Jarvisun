# Optional terminal and screen adapters

These adapters require a caller-authorized workspace. The policy broker must independently authorize invocation; model text is never authority. They support bounded container commands and offline HTML screenshots, rather than general host-shell, online browser, or operating-system desktop control.

## DockerWorker

```python
worker = DockerWorker(workspace, "owner-approved-image@sha256:<64 hex characters>")
worker.available()                 # Read-only daemon/local-image inspection
worker.run(["python", "script.py"], timeout=30)
```

`available()` reports `available` and a safe reason when Docker or the exact local image is missing. No image is pulled or built automatically. `run()` validates argv before checking availability, accepts at most 64 arguments of 8,192 characters each, rejects shell entrypoints, and uses subprocess argv with `shell=False`. Commands can execute programs inside their authorized container; the adapter never executes the provided command as a host shell.

The Docker command uses a pinned SHA-256 image, `--pull=never`, `--network=none`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, read-only root, 512 MB memory/swap bound, 64 PIDs, one CPU, and an explicit UID/GID. When Jarvis itself is root, the container uses UID/GID 65534. Only the authorized workspace is mounted, read/write at `/workspace`; `/tmp` is a 64 MB `noexec,nosuid` tmpfs. Credentials and proxy variables are not inherited. Private paths such as `.env`, `.ssh`, `.git`, and `access-token`, symlinks, and workspaces above a 10,000-entry inspection budget are rejected before execution. Do not place secrets in this workspace. Root-owned private workspace permissions may prevent UID 65534 from writing; provision appropriate ownership for an intended worker rather than granting host-root access.

Execution is capped at 64 KB combined stdout/stderr and a caller timeout up to 120 seconds. Overflow/timeout kills the CLI and attempts forced removal of its named container; cleanup failure cannot be considered a guarantee of termination. Docker daemon permissions remain powerful host authority: these flags reduce exposure but do not establish a hardened security boundary against a malicious runtime/kernel exploit. Digest pinning establishes artifact identity, not trustworthiness. Only use an independently trusted image and workspace.

Receipts include `verified`, `result`, exit status, timeout/truncation flags, and image. `verified: true` means the command exited zero within execution/output limits; it does not prove the command achieved a user's business goal. Missing capability returns `verified: false` with an explicit reason. Production policy must authorize worker access and verify relevant resulting files separately.

## OfflineBrowser

```python
browser = OfflineBrowser(workspace)  # Uses detected system Chromium or Playwright's installed browser
browser.render(workspace / "generated.html", workspace / "capture.png")
```

An optional explicit `executable_path` selects an owner-approved browser executable. Inputs must be existing `.html`/`.htm` files under the workspace, at most 2 MB. Outputs must be a new `.png` in an existing workspace directory. Traversal, symlinks, private paths, and overwrites are denied. Use trusted locally generated HTML. The browser receives HTML through `set_content`, not a `file://` navigation, so it has no authorized local-file page origin. External asset requests are aborted, WebSockets are closed where supported, service workers and downloads are disabled, and JavaScript is disabled. Inline CSS and data images can render; live websites, JavaScript applications, external fonts/assets, remote authentication, and online screenshots are intentionally unsupported.

Each render uses a fresh browser/context with no credentials passed to its process. The viewport is 1280×800; captures are bounded to that viewport. Launch is limited to 15 seconds and page/render operations to 10 seconds each. PNG signature and output creation are verified, with output mode 0600. Success returns `verified`, workspace-relative `path`, byte count, blocked-request count, and `javascript_enabled: false`. Missing Playwright/Chromium or a render failure returns an explicit unverified receipt. The network rules protect browser page traffic; Chromium is not an OS sandbox, and Playwright's Chromium sandbox is not enabled in this container. Online navigation would require an independently enforced egress policy before implementation.

## Validation, 2026-10-10

Contract tests verify the pinned image, mount/network/resource flags, private path denials, missing capability receipts, and actual host subprocess output/time bounds. Docker availability is inspected without creating containers; no live Docker command was run here.

The installed Playwright-managed Chromium binary was absent, and its official download URL on `cdn.playwright.dev` returned an HTTPS proxy CONNECT 403. A preinstalled system `/usr/bin/chromium` was subsequently identified and used instead, without downloading artifacts. The real offline browser test rendered HTML into a PNG, attempted an external image request and verified it was blocked, and confirmed JavaScript remained disabled. This is an actual screen-adapter check, not a mocked screenshot or proof of online browsing/desktop access.
