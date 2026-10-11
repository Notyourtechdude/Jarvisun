import pytest


def test_private_state_inside_workspace_cannot_be_read_or_listed(tmp_path):
    from jarvis.config import Settings
    from jarvis.security import owner_token
    from jarvis.tools import ToolService
    settings = Settings(workspace=tmp_path / "work", data_dir=tmp_path / "work" / "private")
    owner_token(settings)
    tools = ToolService(settings)
    (settings.data_dir / "note.txt").write_text("private")
    with pytest.raises(ValueError):
        tools.execute("read_file", {"path": "private/note.txt"})
    assert "private" not in [r["name"] for r in tools.execute("list_files", {})["files"]]


def test_hardlink_and_authorized_credential_root_do_not_bypass_policy(tmp_path):
    from jarvis.config import Settings
    from jarvis.tools import ToolService
    root = tmp_path / ".ssh"
    root.mkdir()
    (root / "secret.txt").write_text("secret")
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work", vault_roots=[root])
    settings.prepare()
    tools = ToolService(settings)
    with pytest.raises(ValueError):
        tools.execute("read_file", {"path": str(root / "secret.txt")})
    (settings.workspace / "linked").hardlink_to(root / "secret.txt")
    with pytest.raises(ValueError):
        tools.execute("read_file", {"path": "linked"})


def test_calculator_and_returns_reject_huge_decimal_exponents():
    from jarvis.quality import calculate, rank_options
    with pytest.raises(ValueError):
        calculate("1e-100000000")
    with pytest.raises(ValueError):
        rank_options([{"name": "bad", "benefit": "1e-100000000"}])


def test_numeric_invalid_types_are_reported_as_validation_errors():
    from jarvis.quality import rank_options
    with pytest.raises(ValueError):
        rank_options([{"benefit": "not-a-number"}])
