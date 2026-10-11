"""Bounded private snapshots and staged, validated portable restoration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .config import Settings
from .security import owner_token
from .store import Store
from .vault import _no_symlinks, _open_directory, _secret

_MAX_TOTAL = 100_000_000
_MAX_ENTRIES = 10_000
_MAX_MANIFEST = 4_000_000
_FORMAT = "jarvis-backup"
_VERSION = 1
_CONFIG_KEYS = {
    "model_name",
    "max_tokens",
    "request_timeout",
    "max_file_bytes",
    "quiet_start",
    "quiet_end",
    "timezone",
    "daily_call_limit",
    "daily_post_limit",
    "daily_message_limit",
    "allowed_tools",
}
_DATABASES = {"state/state.sqlite3", "state/vault.sqlite3", "state/effects.sqlite3"}


def _name(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("Invalid archive path")
    parts = name.split("/")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Unsafe archive path")
    if name == "manifest.json":
        return path
    if name not in _DATABASES and (len(parts) < 2 or parts[0] != "workspace"):
        raise ValueError("Unsupported archive entry")
    if any(_secret(part) for part in parts) or name.endswith(("-wal", "-shm", "-journal")):
        raise ValueError("Credentials and database journals are excluded")
    return path


def _private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)


def _portable_config(settings: Settings) -> dict:
    config = {key: getattr(settings, key) for key in sorted(_CONFIG_KEYS)}
    safe_tools = set(Settings().allowed_tools)
    config["allowed_tools"] = [tool for tool in settings.allowed_tools if tool in safe_tools]
    return config


def _settings(config: dict, directory: Path) -> Settings:
    if not isinstance(config, dict) or set(config) - _CONFIG_KEYS:
        raise ValueError("Nonportable configuration")
    config = dict(config)
    if "allowed_tools" in config:
        default_tools = set(Settings().allowed_tools)
        if not isinstance(config["allowed_tools"], list) or any(
            not isinstance(tool, str) or tool not in default_tools for tool in config["allowed_tools"]
        ):
            raise ValueError("Unsupported restored tool permissions")
    return Settings(
        **config,
        data_dir=directory / "state",
        workspace=directory / "workspace",
        vault_roots=[directory / "workspace"],
    )


def _database_snapshot(source: Path, destination: Path) -> None:
    _no_symlinks(source)
    info = source.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Database links and special files are excluded")
    if info.st_size > _MAX_TOTAL:
        raise ValueError("Database exceeds backup size limit")
    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=15) as origin,
        sqlite3.connect(destination) as target,
    ):
        page_size = origin.execute("PRAGMA page_size").fetchone()[0]

        def bounded_progress(status, remaining, pages):
            if pages * page_size > _MAX_TOTAL:
                raise ValueError("Database exceeds snapshot size limit")

        origin.backup(target, pages=256, progress=bounded_progress)
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        target.execute("PRAGMA journal_mode=DELETE")
    destination.chmod(0o600)


def _sanitize_vault(path: Path, workspace: Path, allowed_files: set[str]) -> None:
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        rows = db.execute("SELECT id, path FROM sources").fetchall()
        for source, raw in rows:
            original = Path(raw)
            try:
                relative = original.relative_to(workspace)
                archive_name = "workspace/" + relative.as_posix()
                _name(archive_name)
            except ValueError:
                archive_name = ""
            if archive_name not in allowed_files:
                db.execute(
                    "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE source=?)",
                    (source,),
                )
                db.execute("DELETE FROM sources WHERE id=?", (source,))
            else:
                db.execute(
                    "UPDATE sources SET root='workspace', path=?, relative_path=? WHERE id=?",
                    (archive_name, relative.as_posix(), source),
                )
        db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    with sqlite3.connect(path) as db:
        db.execute("VACUUM")


def backup(settings: Settings, destination: Path) -> dict:
    """Snapshot online databases and scoped noncredential workspace files to ZIP."""
    destination = Path(os.path.abspath(destination))
    _no_symlinks(destination)
    if destination.exists():
        raise ValueError("Backup destination already exists")
    _no_symlinks(settings.workspace)
    _no_symlinks(settings.data_dir)
    if not destination.parent.is_dir():
        raise ValueError("Backup parent must already exist")
    files = {}
    total = 0
    with tempfile.TemporaryDirectory(prefix="jarvis-backup-") as temporary:
        staging = Path(temporary)
        root_fd = _open_directory(settings.workspace)
        try:

            def walk_error(error):
                raise ValueError("Cannot read workspace") from error

            for directory, dirs, names, directory_fd in os.fwalk(
                ".", dir_fd=root_fd, follow_symlinks=False, onerror=walk_error
            ):
                for name in dirs[:]:
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        raise ValueError("Workspace symlinks are excluded")
                    child = settings.workspace / directory / name
                    if _secret(name) or (
                        child == settings.data_dir and settings.data_dir != settings.workspace
                    ):
                        dirs.remove(name)
                for name in names:
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
                        raise ValueError("Workspace links and special files are excluded")
                    relative = (Path(directory) / name).as_posix()
                    archive_name = "workspace/" + relative
                    try:
                        _name(archive_name)
                    except ValueError:
                        continue
                    if name.endswith((".sqlite", ".sqlite3", ".db")):
                        continue  # Raw workspace databases lack a consistent online snapshot.
                    if info.st_size > settings.max_file_bytes or total + info.st_size > _MAX_TOTAL:
                        raise ValueError("Workspace exceeds backup size limits")
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                    with os.fdopen(fd, "rb") as file:
                        current = os.fstat(file.fileno())
                        if current.st_nlink != 1 or not stat.S_ISREG(current.st_mode):
                            raise ValueError("Workspace file changed into a link")
                        data = file.read(min(settings.max_file_bytes, _MAX_TOTAL - total) + 1)
                    if len(data) > settings.max_file_bytes or total + len(data) > _MAX_TOTAL:
                        raise ValueError("Workspace exceeds backup size limits")
                    _private_write(staging / archive_name, data)
                    files[archive_name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    total += len(data)
                    if len(files) > _MAX_ENTRIES - 4:
                        raise ValueError("Too many backup entries")
        finally:
            os.close(root_fd)
        workspace_files = set(files)
        for name in ("state.sqlite3", "vault.sqlite3", "effects.sqlite3"):
            source = settings.data_dir / name
            if not source.exists():
                if source.is_symlink():
                    raise ValueError("Database symlink is excluded")
                continue
            archive_name = "state/" + name
            target = staging / archive_name
            target.parent.mkdir(mode=0o700, exist_ok=True)
            _database_snapshot(source, target)
            if name == "vault.sqlite3":
                _sanitize_vault(target, settings.workspace, workspace_files)
            if total + target.stat().st_size > _MAX_TOTAL:
                raise ValueError("Backup exceeds total size limit")
            data = target.read_bytes()
            total += len(data)
            files[archive_name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        manifest = {
            "format": _FORMAT,
            "version": _VERSION,
            "config": _portable_config(settings),
            "files": files,
        }
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
        if len(encoded) > _MAX_MANIFEST or total + len(encoded) > _MAX_TOTAL:
            raise ValueError("Manifest exceeds backup size limit")
        _private_write(staging / "manifest.json", encoded)
        output = staging / "backup.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for name in ["manifest.json", *sorted(files)]:
                zipped.write(staging / name, arcname=name)
        # O_EXCL publishes the final archive without replacing any destination.
        output_data = output.read_bytes()
        _private_write(destination, output_data)
    return {"path": str(destination), "files": len(files), "bytes": total, "version": _VERSION}


def _validate_archive(archive: Path, directory: Path) -> tuple[dict, dict[str, bytes]]:
    _no_symlinks(archive)
    if (
        archive.stat().st_nlink != 1
        or not archive.is_file()
        or archive.stat().st_size > _MAX_TOTAL + 5_000_000
    ):
        raise ValueError("Invalid archive file")
    payload = {}
    try:
        with zipfile.ZipFile(archive) as zipped:
            entries = zipped.infolist()
            if len(entries) > _MAX_ENTRIES or not entries:
                raise ValueError("Invalid archive entry count")
            seen = set()
            total = 0
            for entry in entries:
                _name(entry.filename)
                mode = entry.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (
                    entry.filename in seen
                    or entry.orig_filename != entry.filename
                    or entry.is_dir()
                    or kind not in {0, stat.S_IFREG}
                    or entry.flag_bits & 1
                ):
                    raise ValueError("Duplicate, linked, or encrypted archive entry")
                seen.add(entry.filename)
                total += entry.file_size
                if total > _MAX_TOTAL or entry.file_size < 0:
                    raise ValueError("Archive expands beyond size limit")
                if entry.filename == "manifest.json" and entry.file_size > _MAX_MANIFEST:
                    raise ValueError("Manifest exceeds size limit")
            if "manifest.json" not in seen:
                raise ValueError("Missing archive manifest")
            for name in seen:
                if any(str(parent) in seen for parent in PurePosixPath(name).parents if str(parent) != "."):
                    raise ValueError("Archive file/directory collision")
            for entry in entries:
                with zipped.open(entry) as file:
                    data = file.read(entry.file_size + 1)
                if len(data) != entry.file_size:
                    raise ValueError("Archive size mismatch")
                payload[entry.filename] = data
    except (zipfile.BadZipFile, OSError, RuntimeError) as error:
        raise ValueError("Could not validate backup archive") from error
    try:
        manifest = json.loads(payload.pop("manifest.json"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != _FORMAT
            or type(manifest.get("version")) is not int
            or manifest["version"] != _VERSION
            or set(manifest) != {"format", "version", "config", "files"}
            or not isinstance(manifest["files"], dict)
            or set(manifest["files"]) != set(payload)
        ):
            raise ValueError("Unsupported or inconsistent manifest")
        _settings(manifest["config"], directory)
        for name, data in payload.items():
            metadata = manifest["files"][name]
            if (
                not isinstance(metadata, dict)
                or set(metadata) != {"size", "sha256"}
                or type(metadata["size"]) is not int
                or metadata["size"] != len(data)
                or not isinstance(metadata["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", metadata["sha256"])
                or hashlib.sha256(data).hexdigest() != metadata["sha256"]
            ):
                raise ValueError("Archive checksum mismatch")
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid archive manifest") from error
    return manifest, payload


def _restore_database(path: Path, workspace: Path) -> int:
    workflows_paused = 0
    try:
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
                raise ValueError("Unexpected executable database schema")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = (
                {"audit", "jobs", "focus"}
                if path.name == "state.sqlite3"
                else {
                    "sources",
                    "chunks",
                    "links",
                    "chunks_fts",
                    "chunks_fts_data",
                    "chunks_fts_idx",
                    "chunks_fts_content",
                    "chunks_fts_docsize",
                    "chunks_fts_config",
                }
            )
            if path.name == "effects.sqlite3":
                required = {"effects", "policies"}
            optional = {"workflows", "poll_cursors"} if path.name == "state.sqlite3" else set()
            if not required <= tables or tables - required - optional:
                raise ValueError("Unsupported restored database schema")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Restored database failed integrity check")
            if path.name == "state.sqlite3" and "workflows" in tables:
                workflows_paused = db.execute("SELECT count(*) FROM workflows").fetchone()[0]
                db.execute("UPDATE workflows SET paused=1")
            if path.name == "vault.sqlite3":
                for source, root, raw, relative in db.execute(
                    "SELECT id, root, path, relative_path FROM sources"
                ).fetchall():
                    portable = "workspace/" + relative
                    _name(portable)
                    if root != "workspace" or raw != portable:
                        raise ValueError("Nonportable vault source path")
                    db.execute(
                        "UPDATE sources SET root=?, path=? WHERE id=?",
                        (str(workspace), str(workspace / relative), source),
                    )
            db.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as error:
        raise ValueError("Invalid restored database") from error
    return workflows_paused


def restore(archive: Path, directory: Path) -> Settings:
    """Validate first; restore into a private sibling, then atomically publish it."""
    archive = Path(os.path.abspath(archive))
    directory = Path(os.path.abspath(directory))
    _no_symlinks(directory)
    if directory.exists() or not directory.parent.is_dir():
        raise ValueError("Restore target must be absent and its parent must exist")
    manifest, payload = _validate_archive(archive, directory)
    staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}-restore-", dir=directory.parent))
    try:
        for name, data in payload.items():
            _private_write(staging / name, data)
        settings = _settings(manifest["config"], staging)
        settings.prepare()
        workflows_paused = 0
        for name in _DATABASES:
            database = staging / name
            if database.exists():
                workflows_paused += _restore_database(database, directory / "workspace")
        Store(settings.data_dir / "state.sqlite3").audit(
            "restore_policy",
            {
                "workflows_paused": workflows_paused,
                "external_roots_disabled": True,
                "remote_integrations_require_rebinding": True,
            },
        )
        owner_token(settings)  # Only the new settings receive a new owner credential.
        values = _portable_config(settings)
        lines = ['data_dir = "state"', 'workspace = "workspace"', 'vault_roots = ["workspace"]']
        for key, value in values.items():
            if isinstance(value, list):
                encoded = "[" + ", ".join(json.dumps(item) for item in value) + "]"
            else:
                encoded = json.dumps(value)
            lines.append(f"{key} = {encoded}")
        _private_write(staging / "jarvis.toml", ("\n".join(lines) + "\n").encode())
        for item in staging.rglob("*"):
            item.chmod(0o700 if item.is_dir() else 0o600)
        _no_symlinks(directory.parent)
        if directory.exists() or directory.is_symlink():
            raise ValueError("Restore target appeared during validation")
        os.rename(staging, directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _settings(manifest["config"], directory)
