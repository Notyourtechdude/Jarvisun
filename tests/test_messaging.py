from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis.store import Store


def test_poll_cursor_survives_restart_without_replaying_an_update(tmp_path):
    from jarvis.messaging import PollCursor
    store = Store(tmp_path / "state.sqlite3")
    first = PollCursor(store, "private bot credential:owner")
    assert first.offset() == 0
    assert first.claim(41)
    resumed = PollCursor(Store(tmp_path / "state.sqlite3"), "private bot credential:owner")
    assert resumed.offset() == 42
    assert not resumed.claim(41)
    assert not resumed.claim(40)
    assert resumed.claim(42)
    assert PollCursor(store, "another bot:owner").offset() == 0
    with store.connect() as db:
        values = str([tuple(row) for row in db.execute("SELECT * FROM poll_cursors")])
        assert "private bot credential" not in values


def test_poll_update_has_only_one_concurrent_claim(tmp_path):
    from jarvis.messaging import PollCursor
    cursor = PollCursor(Store(tmp_path / "state.sqlite3"), "one binding")
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(cursor.claim, [99] * 16)) == 1


def test_private_cursor_database_can_be_backed_up_and_restored(tmp_path):
    from jarvis.config import Settings
    from jarvis.messaging import PollCursor
    from jarvis.portability import backup, restore
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    assert PollCursor(Store(settings.data_dir / "state.sqlite3"), "bot:owner").claim(50)
    archive = tmp_path / "snapshot.zip"
    backup(settings, archive)
    restored = restore(archive, tmp_path / "restored")
    assert PollCursor(Store(restored.data_dir / "state.sqlite3"), "bot:owner").offset() == 51


@pytest.mark.parametrize("value", [True, -1, "42", 2**63 - 1])
def test_cursor_rejects_invalid_provider_update_ids(tmp_path, value):
    from jarvis.messaging import PollCursor
    cursor = PollCursor(Store(tmp_path / "state.sqlite3"), "one binding")
    with pytest.raises(ValueError):
        cursor.claim(value)
    assert cursor.offset() == 0
