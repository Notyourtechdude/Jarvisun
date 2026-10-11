import json
import sys
from pathlib import Path

import pytest

from jarvis.codex_provider import CodexProvider
from jarvis.security import Denied


def fake_codex(tmp_path, body):
    executable = tmp_path / "owner-codex"
    executable.write_text(f"#!{sys.executable}\n" + body)
    executable.chmod(0o700)
    return executable


def auth_home(tmp_path):
    directory = tmp_path / "owner-auth"
    directory.mkdir(mode=0o700)
    return directory


def success_events(text="Suggested next step"):
    return '\n'.join(json.dumps(value) for value in [
        {"type": "thread.started", "thread_id": "test"},
        {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ])


def test_disabled_adapter_never_runs_or_infers_harness_configuration(tmp_path):
    provider = CodexProvider(tmp_path, None, None)
    assert provider.available()["available"] is False
    assert provider.run("hello")["verified"] is False


def test_explicit_executable_and_separate_private_auth_home_required(tmp_path):
    executable = fake_codex(tmp_path, 'print("unused")\n')
    for path in (Path('/workspace/.codex'), Path('/root/.codex'), Path('/home/agent/.codex')):
        with pytest.raises(Denied):
            CodexProvider(tmp_path, executable, path, enabled=True)
    home = auth_home(tmp_path)
    home.chmod(0o755)
    with pytest.raises(Denied):
        CodexProvider(tmp_path, executable, home, enabled=True)
    with pytest.raises(Denied):
        CodexProvider(tmp_path, Path('codex'), home, enabled=True)


def test_inherited_codex_home_alias_is_denied_without_reading_credentials(tmp_path, monkeypatch):
    home = auth_home(tmp_path)
    monkeypatch.setenv('CODEX_HOME', str(home))
    executable = fake_codex(tmp_path, 'print("unused")\n')
    with pytest.raises(Denied):
        CodexProvider(tmp_path, executable, home, enabled=True)


def test_actual_argv_stdin_env_and_empty_task_context(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'never-inherit-test-key')
    home = auth_home(tmp_path)
    code = '''import json,os,sys
prompt=sys.stdin.read()
assert sys.argv[1]=="exec" and sys.argv[-1]=="-"
assert "--json" in sys.argv and "--ephemeral" in sys.argv
assert "--ignore-user-config" in sys.argv and "--ignore-rules" in sys.argv
assert sys.argv[sys.argv.index("--sandbox")+1]=="read-only"
assert "OPENAI_API_KEY" not in os.environ
assert "--skip-git-repo-check" in sys.argv
assert os.environ["CODEX_HOME"].endswith("owner-auth")
cwd=sys.argv[sys.argv.index("--cd")+1]
assert os.getcwd()==cwd and os.listdir(cwd)==[]
print(json.dumps({"type":"item.completed","item":{"id":"i","type":"agent_message","text":prompt}}))
print(json.dumps({"type":"turn.completed","usage":{"output_tokens":1}}))
'''
    executable = fake_codex(tmp_path, code)
    provider = CodexProvider(tmp_path, executable, home, enabled=True)
    assert provider.available()["available"] is True
    result = provider.run('Owner prompt through stdin')
    assert result["completed"] is True, result
    assert result["content"] == 'Owner prompt through stdin'
    assert result["verified"] is False
    assert result["usage"] == {"output_tokens": 1}
    assert "never-inherit" not in str(result)


@pytest.mark.parametrize('output', ['plain private-output', '{}',
                         json.dumps({"type":"error","message":"private-credential"}),
                         json.dumps({"type":"item.completed","item":{"type":"reasoning","text":"secret"}}),
                         json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"incomplete"}})])
def test_error_malformed_or_incomplete_stream_is_not_success_or_log_channel(tmp_path, output):
    executable = fake_codex(tmp_path, 'print(' + repr(output) + ')\n')
    result = CodexProvider(tmp_path, executable, auth_home(tmp_path), enabled=True).run('hello')
    assert result['completed'] is False
    assert result['verified'] is False
    assert 'private' not in str(result) and 'secret' not in str(result)


def test_final_agent_message_only_ignores_intermediate_outputs(tmp_path):
    output = json.dumps({'type':'item.completed','item':{'type':'reasoning','text':'internal-summary'}})+'\n'
    output += success_events('Final suggestion')
    executable = fake_codex(tmp_path, 'print(' + repr(output) + ')\n')
    result = CodexProvider(tmp_path, executable, auth_home(tmp_path), enabled=True).run('hello')
    assert result['content'] == 'Final suggestion'
    assert 'internal-summary' not in str(result)


def test_nonzero_and_overflow_do_not_disclose_subprocess_errors(tmp_path):
    for index, code in enumerate(['import sys;sys.stderr.write("credential-error");sys.exit(1)',
                                 'print("credential-output"*20000)']):
        location = tmp_path / str(index)
        location.mkdir()
        result = CodexProvider(location, fake_codex(location,code), auth_home(location), enabled=True).run('hi')
        assert result['completed'] is False
        assert 'credential' not in str(result)


def test_over_budget_prompt_is_denied_before_executable_runs(tmp_path):
    provider = CodexProvider(tmp_path, None, None)
    with pytest.raises(Denied):
        provider.run('x'*16001)


def test_timeout_is_bounded_and_stderr_is_suppressed(tmp_path, monkeypatch):
    monkeypatch.setattr('jarvis.codex_provider.RUN_TIMEOUT_SECONDS', .1)
    code = 'import time,sys;sys.stderr.write("private-failure");time.sleep(10)'
    executable = fake_codex(tmp_path, code)
    result = CodexProvider(tmp_path, executable, auth_home(tmp_path), enabled=True).run('hello')
    assert result['completed'] is False
    assert 'private' not in str(result)


def test_auth_home_cannot_load_implicit_global_agent_context(tmp_path):
    home = auth_home(tmp_path)
    (home/'AGENTS.md').write_text('owner global instructions')
    with pytest.raises(Denied):
        CodexProvider(tmp_path, fake_codex(tmp_path, 'print("unused")'), home, enabled=True)


def test_unknown_events_do_not_report_success(tmp_path):
    output = json.dumps({'type':'unknown.event','private':'secret'})+'\n'+success_events()
    executable = fake_codex(tmp_path, 'print('+repr(output)+')')
    result = CodexProvider(tmp_path, executable, auth_home(tmp_path), enabled=True).run('hi')
    assert result['completed'] is False
    assert 'secret' not in str(result)
