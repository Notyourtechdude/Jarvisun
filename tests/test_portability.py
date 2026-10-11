"""Real archives and state exercise portable backups and hostile restore inputs."""

import hashlib
import json
import os
import stat
import zipfile

import pytest

from jarvis.config import Settings
from jarvis.focus import FocusService
from jarvis.security import owner_token
from jarvis.store import Store
from jarvis.vault import Vault


def seeded(tmp_path):
    settings = Settings(data_dir=tmp_path / "old" / "state", workspace=tmp_path / "old" / "workspace")
    settings.prepare()
    original_token = owner_token(settings)
    store = Store(settings.data_dir / "state.sqlite3")
    FocusService(store).action("start", "Restore this task", minutes=5)
    (settings.workspace / "note.md").write_text("workspaceword [[Future]]")
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.md").write_text("externalconfidentialword")
    settings.vault_roots = [settings.workspace, external]
    Vault(settings.vault_roots, settings.data_dir / "vault.sqlite3").index()
    return settings, original_token


def write_archive(path, payload=None, **manifest_changes):
    files = payload or {"workspace/note.md": b"safe note"}
    manifest = {
        "format": "jarvis-backup",
        "version": 1,
        "config": {},
        "files": {
            name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        },
    }
    manifest.update(manifest_changes)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def test_live_database_backup_restores_focus_and_workspace_only_vault(tmp_path):
    from jarvis.portability import backup, restore

    settings, original_token = seeded(tmp_path)
    archive = tmp_path / "backup.zip"
    result = backup(settings, archive)
    assert result["path"] == str(archive)
    assert result["files"] >= 3
    assert archive.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        assert "state/state.sqlite3" in names and "state/vault.sqlite3" in names
        assert not any("access-token" in name or name.endswith(("-wal", "-shm")) for name in names)
        assert all(original_token.encode() not in zipped.read(name) for name in names)
        assert all(b"externalconfidentialword" not in zipped.read(name) for name in names)
    target = tmp_path / "restored"
    restored = restore(archive, target)
    assert restored.data_dir == target / "state" and restored.workspace == target / "workspace"
    assert restored.vault_roots == [restored.workspace]
    assert owner_token(restored) != original_token
    assert not restored.allow_cloud and not restored.enable_browser
    focus = FocusService(Store(restored.data_dir / "state.sqlite3")).status()
    assert focus["session"]["task"] == "Restore this task"
    vault = Vault(restored.vault_roots, restored.data_dir / "vault.sqlite3")
    assert vault.search("workspaceword")[0]["path"] == str(restored.workspace / "note.md")
    assert vault.search("externalconfidentialword") == []
    assert (restored.workspace / "note.md").read_text() == "workspaceword [[Future]]"
    assert Settings.load(target / "jarvis.toml").data_dir == restored.data_dir


def test_backup_excludes_secrets_and_rejects_linked_workspace_files(tmp_path):
    from jarvis.portability import backup

    settings, _ = seeded(tmp_path)
    for name in [".env", ".env.local", "access-token", "credentials.json", "google-credentials.json"]:
        (settings.workspace / name).write_text("secretword")
    (settings.workspace / ".ssh").mkdir()
    (settings.workspace / ".ssh" / "key.txt").write_text("secretword")
    archive = tmp_path / "backup.zip"
    backup(settings, archive)
    with zipfile.ZipFile(archive) as zipped:
        assert all(b"secretword" not in zipped.read(name) for name in zipped.namelist())
    linked = settings.workspace / "linked.txt"
    linked.symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ValueError):
        backup(settings, tmp_path / "linked.zip")
    assert not (tmp_path / "linked.zip").exists()
    linked.unlink()
    target = tmp_path / "outside.txt"
    target.write_text("outside")
    os.link(target, linked)
    with pytest.raises(ValueError):
        backup(settings, tmp_path / "hardlinked.zip")


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "/absolute.txt",
        "workspace/../../escape.txt",
        "C:/escape.txt",
        "workspace\\escape.txt",
        "state/access-token",
        "workspace/.env",
        "workspace/.ssh/private.txt",
    ],
)
def test_restore_rejects_unsafe_names_before_creating_destination(tmp_path, name):
    from jarvis.portability import restore

    archive = write_archive(tmp_path / "bad.zip", {name: b"bad"})
    target = tmp_path / "target"
    with pytest.raises(ValueError):
        restore(archive, target)
    assert not target.exists()
    assert not (tmp_path / "escape.txt").exists()


def test_restore_rejects_symlink_and_duplicate_entries(tmp_path):
    from jarvis.portability import restore

    archive = write_archive(tmp_path / "bad.zip")
    with zipfile.ZipFile(archive, "a") as zipped:
        info = zipfile.ZipInfo("workspace/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(info, "../outside")
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "target")
    archive = write_archive(tmp_path / "duplicate.zip")
    with zipfile.ZipFile(archive, "a") as zipped:
        with pytest.warns(UserWarning):
            zipped.writestr("workspace/note.md", "different")
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_restore_verifies_hashes_versions_and_does_not_overwrite(tmp_path):
    from jarvis.portability import restore

    wrong_hash = {"workspace/note.md": {"size": 9, "sha256": "0" * 64}}
    for name, changes in [("checksum", {"files": wrong_hash}), ("version", {"version": 999})]:
        archive = write_archive(tmp_path / f"{name}.zip", **changes)
        with pytest.raises(ValueError):
            restore(archive, tmp_path / name)
        assert not (tmp_path / name).exists()
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "preserve.txt").write_text("preserve")
    archive = write_archive(tmp_path / "valid.zip")
    with pytest.raises(ValueError):
        restore(archive, existing)
    assert (existing / "preserve.txt").read_text() == "preserve"


def test_zip_bomb_and_nonportable_configuration_are_rejected(tmp_path):
    from jarvis.portability import restore

    archive = write_archive(tmp_path / "bomb.zip", {"workspace/large.txt": b"0" * (100_000_001)})
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "bomb")
    archive = write_archive(tmp_path / "roots.zip", config={"vault_roots": ["/etc"], "data_dir": "/etc"})
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_restore_rejects_checksum_valid_invalid_database_without_partial_result(tmp_path):
    from jarvis.portability import restore

    archive = write_archive(tmp_path / "invalid-db.zip", {"state/state.sqlite3": b"not sqlite"})
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()
    assert not list(tmp_path.glob(".target-restore-*"))


def test_backup_does_not_overwrite_existing_destination(tmp_path):
    from jarvis.portability import backup

    settings, _ = seeded(tmp_path)
    target = tmp_path / "keep.zip"
    target.write_bytes(b"keep")
    with pytest.raises(ValueError):
        backup(settings, target)
    assert target.read_bytes() == b"keep"


def test_default_nested_workspace_is_backed_up(tmp_path):
    from jarvis.portability import backup, restore

    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "state" / "workspace")
    settings.prepare()
    (settings.workspace / "note.md").write_text("nested workspace")
    archive = tmp_path / "backup.zip"
    backup(settings, archive)
    restored = restore(archive, tmp_path / "restored")
    assert (restored.workspace / "note.md").read_text() == "nested workspace"


def test_private_state_nested_inside_workspace_is_excluded(tmp_path):
    from jarvis.portability import backup

    settings = Settings(workspace=tmp_path / "workspace", data_dir=tmp_path / "workspace" / "private-state")
    settings.prepare()
    (settings.data_dir / "private.txt").write_text("privatestatemarker")
    (settings.workspace / "safe.txt").write_text("safe")
    archive = tmp_path / "backup.zip"
    backup(settings, archive)
    with zipfile.ZipFile(archive) as zipped:
        assert "workspace/safe.txt" in zipped.namelist()
        assert not any(b"privatestatemarker" in zipped.read(name) for name in zipped.namelist())


def test_restore_rejects_file_directory_collision_as_invalid_archive(tmp_path):
    from jarvis.portability import restore

    archive = write_archive(tmp_path / "collision.zip", {"workspace/a": b"file", "workspace/a/b": b"nested"})
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_archive_entry_count_is_bounded(tmp_path):
    from jarvis.portability import restore

    archive = write_archive(tmp_path / "many.zip", {f"workspace/{i}.txt": b"x" for i in range(10_000)})
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_nested_restore_directories_and_files_are_private(tmp_path):
    from jarvis.portability import restore

    archive = write_archive(tmp_path / "nested.zip", {"workspace/a/b/note.txt": b"private note"})
    old_umask = os.umask(0o022)
    try:
        restore(archive, tmp_path / "target")
    finally:
        os.umask(old_umask)
    for item in (tmp_path / "target").rglob("*"):
        assert item.stat().st_mode & 0o777 == (0o700 if item.is_dir() else 0o600)


def test_restore_rejects_database_triggers(tmp_path):
    import sqlite3

    from jarvis.portability import restore

    database = tmp_path / "hostile.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            "CREATE TABLE unrelated(value); CREATE TRIGGER surprise AFTER INSERT ON unrelated BEGIN DELETE FROM unrelated; END;"
        )
    archive = write_archive(tmp_path / "hostile.zip", {"state/state.sqlite3": database.read_bytes()})
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_restore_rejects_traversal_inside_vault_database(tmp_path):
    import sqlite3

    from jarvis.portability import restore

    database = tmp_path / "hostile.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            "CREATE TABLE sources(id,root,path,relative_path); INSERT INTO sources VALUES('a','workspace','workspace/../../outside','../../outside');"
        )
    archive = write_archive(tmp_path / "hostile.zip", {"state/vault.sqlite3": database.read_bytes()})
    with pytest.raises(ValueError):
        restore(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_effect_receipts_daily_budget_and_pause_survive_restore(tmp_path):
    from jarvis.extensions.common import ExtensionError, Journal, PolicyDenied
    from jarvis.portability import backup, restore

    settings, _ = seeded(tmp_path)
    journal = Journal(settings.data_dir / "effects.sqlite3")
    payload = {"post": "authorized content"}
    assert journal.reserve("social", "post-one", payload, cap=1) is None
    receipt = {"remote_id": "verified-123"}
    journal.complete("social", "post-one", receipt)
    journal.paused("calls", True)
    archive = tmp_path / "backup.zip"
    backup(settings, archive)
    restored = restore(archive, tmp_path / "restored")
    recovered = Journal(restored.data_dir / "effects.sqlite3")
    assert recovered.reserve("social", "post-one", payload, cap=1) == receipt
    with pytest.raises(ExtensionError, match="budget"):
        recovered.reserve("social", "post-two", {"post": "another"}, cap=1)
    with pytest.raises(PolicyDenied, match="paused"):
        recovered.reserve("calls", "call-one", {"call": "owner"}, cap=2)


def test_pending_effect_remains_uncertain_across_restore(tmp_path):
    from jarvis.extensions.common import ExtensionError, Journal
    from jarvis.portability import backup, restore

    settings, _ = seeded(tmp_path)
    journal = Journal(settings.data_dir / "effects.sqlite3")
    journal.reserve("calls", "call-one", {"call": "owner"}, cap=2)
    archive = tmp_path / "backup.zip"
    backup(settings, archive)
    restored = restore(archive, tmp_path / "restored")
    with pytest.raises(ExtensionError, match="uncertain"):
        Journal(restored.data_dir / "effects.sqlite3").reserve("calls", "call-one", {"call": "owner"}, cap=2)


def test_restored_workflows_are_paused_and_policy_is_audited(tmp_path):
    from jarvis.jobs import JobService
    from jarvis.portability import backup, restore
    from jarvis.tools import ToolService
    from jarvis.workflows import WorkflowService

    settings, _ = seeded(tmp_path)
    store = Store(settings.data_dir / "state.sqlite3")
    workflows = WorkflowService(store, JobService(store), ToolService(settings))
    workflows.create("Old path", "read_file", {"path": str(settings.workspace / "note.md")}, 60)
    archive = tmp_path / "backup.zip"
    backup(settings, archive)
    restored = restore(archive, tmp_path / "restored")
    restored_store = Store(restored.data_dir / "state.sqlite3")
    recovered = WorkflowService(restored_store, JobService(restored_store), ToolService(restored))
    assert recovered.list()[0]["paused"] is True
    assert recovered.tick() == []
    event = next(event for event in restored_store.events() if event["kind"] == "restore_policy")
    assert event["details"]["workflows_paused"] == 1


def test_optional_notify_permission_is_downgraded_and_message_budget_preserved(tmp_path):
    from jarvis.portability import backup, restore

    settings, _ = seeded(tmp_path)
    settings.allowed_tools.append("notify")
    settings.daily_message_limit = 3
    archive = tmp_path / "backup.zip"
    backup(settings, archive)
    restored = restore(archive, tmp_path / "restored")
    assert "notify" not in restored.allowed_tools
    assert restored.daily_message_limit == 3
