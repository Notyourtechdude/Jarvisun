import pytest


def test_settings_require_local_model_by_default(tmp_path):
    from jarvis.config import Settings
    settings = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "work")
    assert settings.model_url == "http://127.0.0.1:11434/v1"
    assert not settings.allow_cloud


def test_invalid_remote_model_requires_explicit_cloud_opt_in(tmp_path):
    from jarvis.config import Settings
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, workspace=tmp_path, model_url="https://example.org/v1")


def test_token_persists_with_private_permissions(tmp_path):
    from jarvis.config import Settings
    from jarvis.security import owner_token
    settings = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "work")
    token = owner_token(settings)
    assert len(token) >= 32
    assert owner_token(settings) == token
    assert (settings.data_dir / "access-token").stat().st_mode & 0o777 == 0o600


def test_unknown_configuration_rejected(tmp_path):
    from jarvis.config import Settings
    path = tmp_path / "config.toml"
    path.write_text('unknown_option = true\n')
    with pytest.raises(ValueError):
        Settings.load(path)
