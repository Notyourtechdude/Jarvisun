"""Private lexical retrieval with explicit source and Markdown-link provenance."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

from .privacy import is_private

_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv", ".pdf"}
_SYSTEM_ROOTS = (
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/run",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/var/lib",
    "/var/run",
    "/var/log",
    "/var/cache",
    "/var/spool",
)
_WIKILINK = re.compile(r"\[\[([^\]\n]{1,250})\]\]")
_CHUNK_SIZE = 1500
_GRAPH_NODES = 1000
_GRAPH_EDGES = 2000


def _id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _no_symlinks(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("Symlink paths are not authorized")


def _secret(name: str) -> bool:
    return is_private(name)


def _open_directory(path: Path) -> int:
    """Pin every path component with openat; refuse symlink ancestors."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


class Vault:
    """Index only explicit roots. Documents are evidence, never authorization."""

    def __init__(self, roots: list[Path], db_path: Path, max_file_bytes: int = 2_000_000):
        if isinstance(max_file_bytes, bool) or not 1 <= max_file_bytes <= 100_000_000:
            raise ValueError("max_file_bytes must be between 1 and 100000000")
        self.max_file_bytes = max_file_bytes
        self.roots = []
        for raw in roots:
            root = _absolute(Path(raw))
            _no_symlinks(root)
            if (
                root == Path("/")
                or any(root == Path(p) or Path(p) in root.parents for p in _SYSTEM_ROOTS)
                or any(is_private(part, directory=True) for part in root.parts)
            ):
                raise ValueError("Credential and system roots are not authorized")
            if not root.is_dir():
                raise ValueError("Vault roots must be existing directories")
            if root not in self.roots:
                self.roots.append(root)
        self._root_parameters = tuple(str(root) for root in self.roots)
        self._root_clause = "(" + ",".join("?" for _ in self.roots) + ")" if self.roots else "(NULL)"
        self.db_path = _absolute(Path(db_path))
        _no_symlinks(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_fd = _open_directory(self.db_path.parent)
        try:
            fd = os.open(self.db_path.name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("Database must be a regular file with one link")
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, root TEXT NOT NULL, path TEXT UNIQUE NOT NULL,
                    relative_path TEXT NOT NULL, digest TEXT NOT NULL, text TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, text TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, text);
                CREATE TABLE IF NOT EXISTS links (
                    source TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    chunk_id TEXT NOT NULL, target_label TEXT NOT NULL, evidence TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS links_source ON links(source);
            """)

    @contextmanager
    def _connection(self):
        _no_symlinks(self.db_path)
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _remove(self, db, source: str) -> None:
        db.execute(
            "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE source=?)", (source,)
        )
        db.execute("DELETE FROM sources WHERE id=?", (source,))

    def _read(self, name: str, directory_fd: int) -> bytes | None:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > self.max_file_bytes:
                return None
            with os.fdopen(fd, "rb", closefd=False) as file:
                data = file.read(self.max_file_bytes + 1)
            return data if len(data) <= self.max_file_bytes else None
        finally:
            os.close(fd)

    def _text(self, data: bytes, extension: str) -> str | None:
        if extension == ".pdf":
            from .document_worker import extract_pdf

            text = extract_pdf(data, max_input_bytes=self.max_file_bytes)
            return text if text is None or len(text) <= self.max_file_bytes else None
        if b"\x00" in data:
            return None
        text = data.decode("utf-8-sig")
        if extension == ".json":
            json.loads(text)  # Do not silently treat malformed JSON as validated data.
        return text

    def _save(self, db, root: Path, path: Path, digest: str, text: str) -> None:
        source = _id(str(path))
        self._remove(db, source)
        db.execute(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)",
            (source, str(root), str(path), str(path.relative_to(root)), digest, text),
        )
        for ordinal, offset in enumerate(range(0, len(text), _CHUNK_SIZE)):
            chunk = f"{source}:{ordinal}"
            content = text[offset : offset + _CHUNK_SIZE]
            db.execute("INSERT INTO chunks VALUES (?, ?, ?, ?)", (chunk, source, ordinal, content))
            db.execute("INSERT INTO chunks_fts(chunk_id, text) VALUES (?, ?)", (chunk, content))
        if path.suffix.lower() in {".md", ".markdown"}:
            for match in _WIKILINK.finditer(text):
                label = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
                if not label:
                    continue
                chunk = f"{source}:{match.start() // _CHUNK_SIZE}"
                db.execute(
                    "INSERT INTO links VALUES (?, ?, ?, ?)",
                    (source, chunk, label, text[max(0, match.start() - 80) : match.end() + 80]),
                )

    def index(self) -> dict:
        """Hash-check files and reconcile removals in one atomic SQLite transaction."""
        counts = {"indexed": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = {
                row["path"]: dict(row) for row in db.execute("SELECT id, root, path, digest FROM sources")
            }
            seen = set()
            for root in self.roots:
                try:
                    root_fd = _open_directory(root)
                except OSError:
                    counts["errors"] += 1
                    continue
                try:

                    def walk_error(error):
                        counts["errors"] += 1

                    for directory, dirs, names, directory_fd in os.fwalk(
                        ".", topdown=True, follow_symlinks=False, dir_fd=root_fd, onerror=walk_error
                    ):
                        for name in dirs[:]:
                            if _secret(name) or stat.S_ISLNK(
                                os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
                            ):
                                dirs.remove(name)
                                counts["skipped"] += 1
                        for name in sorted(names):
                            path = root / directory / name
                            extension = path.suffix.lower()
                            if _secret(name) or extension not in _EXTENSIONS or path == self.db_path:
                                counts["skipped"] += 1
                                continue
                            try:
                                if stat.S_ISLNK(
                                    os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
                                ):
                                    counts["skipped"] += 1
                                    continue
                                data = self._read(name, directory_fd)
                                if data is None:
                                    counts["skipped"] += 1
                                    continue
                                digest = hashlib.sha256(data).hexdigest()
                                previous = existing.get(str(path))
                                if previous and previous["digest"] == digest:
                                    seen.add(str(path))
                                    counts["skipped"] += 1
                                    continue
                                text = self._text(data, extension)
                                if text is None:
                                    counts["skipped"] += 1
                                    continue
                                self._save(db, root, path, digest, text)
                                seen.add(str(path))
                                counts["updated" if previous else "indexed"] += 1
                            except (OSError, ValueError, UnicodeError, RecursionError):
                                counts["errors"] += 1
                finally:
                    os.close(root_fd)
            for path, previous in existing.items():
                if path not in seen:
                    self._remove(db, previous["id"])
                    counts["deleted"] += 1
        return counts

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Treat input as literal words, never FTS operators or SQL."""
        if not isinstance(query, str):
            raise TypeError("query must be text")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        words = re.findall(r"\w+", query[:4000], flags=re.UNICODE)[:32]
        if not words or limit == 0:
            return []
        expression = " AND ".join('"' + word.replace('"', '""') + '"' for word in words)
        with self._connection() as db:
            rows = db.execute(
                f"""
                SELECT s.id AS source, s.path, c.id AS chunk_id, c.text, bm25(chunks_fts) AS score
                FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.chunk_id
                JOIN sources s ON s.id=c.source WHERE chunks_fts MATCH ? AND s.root IN {self._root_clause}
                ORDER BY score, s.path, c.ordinal LIMIT ?
            """,
                (expression, *self._root_parameters, min(limit, 100)),
            ).fetchall()
        return [dict(row) for row in rows]

    def _graph(self, db) -> dict:
        sources = [
            dict(row)
            for row in db.execute(
                f"SELECT id, root, path, relative_path FROM sources WHERE root IN {self._root_clause} ORDER BY path LIMIT ?",
                (*self._root_parameters, _GRAPH_NODES // 2),
            )
        ]
        nodes = [
            {"id": s["id"], "type": "source", "path": s["path"], "title": Path(s["relative_path"]).stem}
            for s in sources
        ]
        by_id = {s["id"]: s for s in sources}
        targets = {}
        for source in sources:
            for key in {
                Path(source["relative_path"]).stem.casefold(),
                str(Path(source["relative_path"]).with_suffix("")).casefold(),
            }:
                targets.setdefault((source["root"], key), []).append(source["id"])
        edges = []
        unresolved = set()
        for row in db.execute(
            "SELECT source, chunk_id, target_label, evidence FROM links ORDER BY source, chunk_id LIMIT ?",
            (_GRAPH_EDGES,),
        ):
            edge = dict(row)
            owner = by_id.get(edge["source"])
            if owner is None:
                continue
            label = Path(edge["target_label"])
            if label.suffix.lower() in {".md", ".markdown"}:
                label = label.with_suffix("")
            key = str(label).casefold()
            relative_key = os.path.normpath(str(Path(owner["relative_path"]).parent / label)).casefold()
            candidates = targets.get((owner["root"], relative_key), [])
            if not candidates:
                candidates = targets.get((owner["root"], key), [])
            target = candidates[0] if len(candidates) == 1 else "link:" + _id(owner["root"] + ":" + key)
            if target.startswith("link:") and target not in unresolved:
                if len(nodes) >= _GRAPH_NODES:
                    continue
                unresolved.add(target)
                nodes.append({"id": target, "type": "unresolved", "title": edge["target_label"]})
            edge.update(target=target, type="wikilink")
            edges.append(edge)
        return {"nodes": nodes, "edges": edges}

    def graph(self) -> dict:
        """Return at most 1000 nodes and 2000 quoted, evidence-backed link edges."""
        with self._connection() as db:
            db.execute("BEGIN")
            return self._graph(db)

    def export(self, destination: Path) -> dict:
        """Write private portable Markdown; caller must authorize the destination."""
        destination = _absolute(Path(destination))
        _no_symlinks(destination)
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise ValueError("Export destination must be absent or empty")
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        _no_symlinks(destination)
        sources_dir = destination / "sources"
        sources_dir.mkdir(mode=0o700)
        with self._connection() as db:
            db.execute("BEGIN")
            sources = [
                dict(row)
                for row in db.execute(
                    f"SELECT id, relative_path, text FROM sources WHERE root IN {self._root_clause} ORDER BY path",
                    self._root_parameters,
                )
            ]
            graph = self._graph(db)

        def write(path: Path, text: str):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(text)

        for source in sources:
            # JSON quoting is valid YAML and keeps untrusted filenames out of metadata syntax.
            metadata = (
                f"---\nsource_id: {source['id']}\n"
                f"relative_path: {json.dumps(source['relative_path'])}\n---\n\n"
            )
            write(sources_dir / f"{source['id']}.md", metadata + source["text"])
        lines = [
            "# Private vault export",
            "",
            "Source IDs identify indexed files. Links record literal Markdown evidence.",
            "",
        ]
        for source in sources:
            label = source["relative_path"].replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
            lines.append(f"- [{label}](sources/{source['id']}.md)")
        lines.extend(["", "## Evidence links", ""])
        for edge in graph["edges"]:
            label = edge["target_label"].replace("\n", " ")
            lines.append(f"- `{edge['source']}` → {label}; chunk `{edge['chunk_id']}`")
        write(destination / "index.md", "\n".join(lines) + "\n")
        # This representation omits machine-specific absolute paths.
        portable_graph = {
            "nodes": [{k: v for k, v in n.items() if k != "path"} for n in graph["nodes"]],
            "edges": graph["edges"],
        }
        write(destination / "graph.json", json.dumps(portable_graph, ensure_ascii=False, indent=2) + "\n")
        return {"path": str(destination), "sources": len(sources), "edges": len(graph["edges"])}
