# Private document vault

`jarvis.vault.Vault(roots: list[Path], db_path: Path, max_file_bytes: int = 2_000_000)` indexes existing, explicitly configured directories. It requires SQLite FTS5 and a POSIX host with descriptor-relative filesystem operations. The database is mode 0600; newly created state directories use mode 0700. Keep the database outside indexed folders when possible. Search, graph and exports filter by the current instance’s roots even before reindexing; disabling a root immediately revokes its citations. Reindexing then removes the obsolete catalog rows.

```python
from pathlib import Path
from jarvis.vault import Vault

vault = Vault([Path("/home/owner/notes")], Path("/home/owner/.local/share/jarvis/vault.sqlite"))
counts = vault.index()
evidence = vault.search("project roadmap", limit=5)
links = vault.graph()
# The application's permission broker must authorize this destination first.
receipt = vault.export(Path("/home/owner/exports/vault-2026-10-10"))
```

## Interface and evidence

- `index() -> dict`: integer `indexed`, `updated`, `deleted`, `skipped`, and `errors` counts. SHA-256 content checks distinguish unchanged files from edits. A single immediate transaction updates source text, chunks, FTS rows and literal Markdown link evidence together. Deletions, files newly excluded by policy, and unreadable/unavailable sources invalidate their derived evidence. Errors do not retain stale citations; repairing access and reindexing restores available sources. Counts of skipped directories are per directory, rather than the count of every excluded descendant.
- `search(query: str, limit: int = 5) -> list[dict]`: records contain `source` (source ID), `path` (absolute source path), `chunk_id`, `text`, and SQLite BM25 `score`. Text is a contiguous original chunk of at most 1500 characters. Source IDs remain stable for a path on the same host; chunk ordinals can change after edits. Queries are literal Unicode words joined with AND. FTS operators are treated as words; punctuation is stripped. Empty/punctuation-only input returns no results. Queries are capped at 4000 characters/32 words, and results at 100. SQL arguments are bound parameters.
- `graph() -> dict`: `nodes` and `edges`. Source nodes contain `id`, `type`, `path`, `title`; unresolved nodes contain `id`, `type`, `title`. Edges contain `source`, `target`, `target_label`, `chunk_id`, `evidence`, `type: wikilink`. Every edge quotes an actual `[[target|alias]]` occurrence in Markdown. Links resolve within their root using relative paths or unique names; ambiguous/missing targets stay explicitly unresolved. Plain text and inferred semantic similarity do not create edges. Responses include at most 500 source nodes, 1000 total nodes, and 2000 edges; larger graphs are partial.
- `export(destination: Path) -> dict`: receipt contains `path`, `sources`, `edges`. Writes `index.md`, `sources/<source_id>.md`, and `graph.json`, with mode 0600 files and private new directories. Metadata stores relative source paths and stable source IDs. Original Markdown is retained; graph.json preserves explicit resolution/evidence. Graph output uses the same bounds as `graph()`, while all current sources are exported. Destination must be absent or empty; existing files and symlink destinations/ancestors are rejected. Authorization of the destination belongs to the caller's independent broker.

## Index policy

UTF-8 `.txt`, `.md`, `.markdown`, `.json`, and `.csv` files are accepted. JSON must parse; malformed JSON increments `errors`. Binary NUL-containing text, oversized files and unsupported extensions are skipped. CSV is preserved as source text; it is not interpreted as validated business data.

PDF support is optional (`uv sync --extra documents`). The installed `pypdf` extracts actual PDF page text; image-only/scanned PDFs receive no OCR. Encrypted PDFs are skipped. PDF input uses the same byte cap; extraction reads at most 200 pages and rejects extracted output above the byte-limit count in characters. Malformed PDFs increment `errors` and do not prevent other documents from indexing.

Filesystem roots and all their ancestors must be actual directories. Symlinks are rejected even if their target is inside a permitted root. Files with multiple hardlinks are excluded because another path could reference content outside the permitted root. Traversal pins root/directory descriptors and opens files relative to them with no-follow flags; special devices and pipes are never read as regular documents. `/`, `/etc`, `/proc`, `/sys`, `/dev`, `/boot`, `/run`, `/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`, `/var/lib`, `/var/run`, `/var/log`, `/var/cache`, `/var/spool`, and credential directories are refused as roots. The database itself is excluded.

`.git`, `.ssh`, `.aws`, `.gnupg`, `.azure`, and `.kube` directories are excluded, together with `.env` variants, credential/secret/access-token filename patterns, token files, `.npmrc`, `.netrc`, shell history, common SSH private-key names, and private key/certificate extensions. The shared `privacy.is_private` policy also protects broker/worker/backup paths. This policy cannot recognize every arbitrary filename containing a secret. Configure roots as document folders, rather than a broad home directory, and keep credentials outside them.

The database contains source content and paths. FTS search is lexical retrieval, not factual verification. Old evidence persists until `index()` runs again; this component does not watch the filesystem. The implementation provides no encryption at rest, OS process sandbox, or protection from malware running as the same owner. PDF parsing runs in a separate isolated Python process with a stripped environment, private temporary working directory, 5-second CPU limit, 512 MiB address-space limit, 8 MB temporary output limit and a 10-second parent timeout. POSIX resource limits apply before the parser imports or reads input. This limits resource consumption; it is not a full security sandbox or an OS privilege/filesystem/network boundary. Concurrent database operations use SQLite transaction isolation and a 15-second busy timeout; this is not a distributed index.

## Verification

`uv run pytest tests/test_vault.py -q` exercises real indexing, edits/deletions, root removal and symlink replacement, size/binary exclusions, secrets and hardlinks, query punctuation/injection, citable bounded text, persistence, graph resolution/limits, private database permissions, portable export and overwrite refusal. `uv run pytest tests/test_documents.py -q` additionally verifies real child extraction, malformed documents, input/output caps, child timeout, stripped credential environment, active CPU/memory/file limits and allocation denial. Optional PDF tests use a real generated text PDF and a malformed document; they skip only if pypdf is unavailable. `uv run ruff check src/jarvis/vault.py tests/test_vault.py` checks the owned implementation and tests.
