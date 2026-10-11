# Private backup and restore

`jarvis.portability.backup(settings: Settings, destination: Path) -> dict` creates a private ZIP archive. The receipt contains `path`, `files`, `bytes` (payload bytes, excluding the manifest/container), and `version`. The output parent must exist; an existing output is refused.

`jarvis.portability.restore(archive: Path, directory: Path) -> Settings` validates a backup and returns settings for a new installation directory. The target must not exist and its parent must already exist. The restored layout is:

```text
restored/
  jarvis.toml
  state/
    access-token          # Newly generated; never copied from the backup
    state.sqlite3         # If present in the original installation
    vault.sqlite3         # Workspace evidence only
    effects.sqlite3       # External receipts, budgets and pause policies
  workspace/
    ...owner workspace files...
```

The CLI uses `jarvis backup --config /path/to/jarvis.toml --output /path/to/backup.zip`, and `jarvis restore --archive /path/to/backup.zip --directory /path/to/restored`. Start the restored instance with `jarvis serve --config /path/to/restored/jarvis.toml`. Protect the archive as personal data; it is mode 0600, but is not encrypted. The restored token is available only in its private state file and is not printed by these APIs.

## What transfers

SQLite's online backup API captures committed `state.sqlite3`, `vault.sqlite3` and `effects.sqlite3` without copying live WAL/journal files. Focus sessions, durable job history and audit state transfer in the state snapshot. Ordinary workspace files transfer within configured per-file size limits. Traversed symlinks, hardlinks and special files cause backup rejection. Credential/internal paths, `.env` variants, token/credential filenames, key material, Git data, and raw workspace database/journal files are excluded. Private state nested inside the workspace is excluded from ordinary file copying; a workspace nested inside state is still copied correctly.

External vault folders are not copied. The vault snapshot removes sources outside the workspace and sources absent from the copied workspace. Its derived FTS chunks and links are reconciled; secure deletion, FTS optimization and VACUUM remove excluded content from the resulting snapshot. This changes only the backup copy. Its source paths are portable relative paths and are rebased during restore. The operational state snapshot intentionally contains owner-entered focus tasks and prior job/audit state; filename exclusions do not identify secrets embedded in arbitrary document or job text.

A versioned JSON manifest records SHA-256 checksums and byte sizes. Maximum expanded payload plus manifest is 100 MB; archives contain at most 10,000 entries. Individual workspace files respect `Settings.max_file_bytes`. Oversized input fails rather than silently producing a partial successful backup. Empty directories, unrelated workspace SQLite files and files excluded by credential policy do not transfer.

Restored state/workspace paths always point under the new target. The only initial vault root is the restored workspace. Old external roots, cloud destinations, host/domain permissions, browser enablement, phone/chat destinations and environment credential bindings are not copied. Local model name and basic budgets/quiet-hour settings transfer; the runtime endpoint returns to the safe local default. Tool permissions transfer only for core supported tools; optional outbound permissions such as `notify` are disabled during backup. Message/call/post daily limits transfer alongside the effect journal. Model weights, host drivers, browser runtimes, OS permissions, platform accounts and credentials require installation/rebinding on the destination machine.

## Restore validation

Every archive entry name is checked before staging writes. Absolute paths, traversal, backslashes/drive paths, credential names, unknown layouts, duplicate entries, symlink/special entries, file/directory collisions, encrypted entries, and archives exceeding expansion/count limits are refused. Manifest format/version, configuration allowlist, entry inventory, byte sizes and checksums must agree. Checksums detect corruption; they are not authentication or a digital signature.

Files are written into a private temporary sibling directory. SQLite integrity, expected schema, absence of triggers/views and portable vault paths are checked before publication. A fresh owner credential is generated through `security.owner_token` for the staged installation. Configuration is serialized as relative paths in `jarvis.toml`. Files are mode 0600 and restored directories are mode 0700. Failure removes staging and leaves the final target absent. Successful staging is published by a same-filesystem directory rename. Do not concurrently mutate the target parent or run active workers against the restore location.

The individual database snapshots are transactionally consistent. This does not make multiple databases and a changing workspace one cross-resource transaction; pause work when an application-wide point-in-time snapshot is required. External-effect receipts, pending outcomes, daily usage counters and pause policies transfer without resetting their idempotency or budgets. Every restored recurring workflow is paused, with a `restore_policy` audit event recording the count. Review/recreate its arguments and destinations before enabling it on the new host. Restored jobs may still need external outcome reconciliation before retrying prior work. The archive parser and SQLite checks run in-process and do not claim an OS sandbox against every possible library-level malicious input.

## Evidence and machine migration

`uv run pytest tests/test_portability.py -q` exercises real ZIP archives, live SQLite snapshots, restoration of focus/workspace retrieval, new token generation, external-source removal, nested workspace/state arrangements, secrets, symlinks/hardlinks, traversal, archive duplicates/collisions, oversized expansion/counts, bad hashes/versions/configuration, malicious database schema/paths, existing-target refusal and private permissions with a permissive umask.

These tests restore into a second directory on the same host. They demonstrate data/configuration portability within that environment. They do not prove operation on a fresh machine or different OS. Fresh-machine validation requires installing the locked Python dependencies, restoring the archive, rebinding credentials/host capabilities, starting the service with restored `jarvis.toml`, and exercising retrieval, focus and the specifically enabled integrations there.
