import json
import subprocess
import sys


def call(*args):
    return subprocess.run([sys.executable, "-m", "jarvis.cli", *map(str, args)],
                          capture_output=True, text=True, timeout=15)


def test_init_and_doctor_and_no_overwrite(tmp_path):
    directory = tmp_path / "jarvis"
    result = call("init", "--directory", directory)
    assert result.returncode == 0, result.stderr
    config = directory / "config.toml"
    assert config.exists()
    token = (directory / "state" / "access-token").read_text()
    assert token not in result.stdout
    result = call("doctor", "--config", config)
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    assert info["python"]
    assert info["credentials"]["TELEGRAM_BOT_TOKEN"] in {"set", "missing"}
    assert token not in result.stdout
    original = config.read_text()
    assert call("init", "--directory", directory).returncode == 0
    assert config.read_text() == original


def test_cli_invalid_config_has_nonzero_exit(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text('allow_cloud = false\nmodel_url="https://remote.example/v1"\n')
    assert call("doctor", "--config", config).returncode != 0


def test_frontier_cli_requires_explicit_enable_and_never_echoes_prompt(tmp_path):
    directory = tmp_path / "jarvis"
    assert call("init", "--directory", directory).returncode == 0
    prompt = "private text that must not appear in an unavailable receipt"
    result = subprocess.run(
        [sys.executable, "-m", "jarvis.cli", "frontier", "--config", str(directory / "config.toml")],
        input=prompt, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["completed"] is False
    assert receipt["verified"] is False
    assert prompt not in result.stdout + result.stderr
