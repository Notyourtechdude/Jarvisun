"""HTTP contract and failure tests; no installed model or external calls required."""
import asyncio
import base64
import json

import httpx
import pytest

from jarvis.config import Settings
from jarvis.models import LocalModel, ModelUnavailable


def run(awaitable):
    return asyncio.run(awaitable)


def settings(**kwargs):
    return Settings(**kwargs)


def response(content="Hello", **kwargs):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                     "model": "qwen3:8b", "usage": {"completion_tokens": 1}, **kwargs})


def test_chat_actual_openai_contract_and_unverified_receipt():
    requests = []
    def handle(request):
        requests.append(request)
        return response()
    model = LocalModel(settings(), transport=httpx.MockTransport(handle))
    result = run(model.chat([{"role": "user", "content": "Hi"}]))
    request = requests[0]
    assert request.url == "http://127.0.0.1:11434/v1/chat/completions"
    assert json.loads(request.content) == {"model": "qwen3:8b", "messages": [{"role": "user", "content": "Hi"}],
                                           "max_tokens": 512, "stream": False}
    assert result["content"] == "Hello"
    assert result["model"] == "qwen3:8b"
    assert result["usage"] == {"completion_tokens": 1}
    assert result["elapsed_ms"] >= 0
    assert result["verified"] is False


def test_status_checks_selected_model_not_only_list_endpoint():
    model = LocalModel(settings(), transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"data": [{"id": "qwen3:8b"}]})))
    assert run(model.status())["available"] is True
    model = LocalModel(settings(), transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"data": [{"id": "other-model"}]})))
    assert run(model.status())["available"] is False


def test_status_and_chat_do_not_leak_backend_error_or_credentials(monkeypatch):
    monkeypatch.setenv("JARVIS_MODEL_API_KEY", "secret-marker")
    model = LocalModel(settings(), transport=httpx.MockTransport(lambda request: httpx.Response(
        401, text="secret-marker user-secret sensitive backend detail")))
    with pytest.raises(ModelUnavailable) as error:
        run(model.chat([{"role": "user", "content": "hello"}]))
    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None
    status = run(model.status())
    assert status["available"] is False
    assert "secret" not in str(status)


def test_remote_requires_explicit_opt_in_and_https_even_for_unvalidated_settings():
    for update in ({"model_url": "https://example.org/v1"},
                   {"model_url": "http://example.org/v1", "allow_cloud": True}):
        with pytest.raises(ModelUnavailable):
            LocalModel(settings().model_copy(update=update))


def test_cloud_only_uses_configured_secret_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_MODEL_API_KEY", "configured-key")
    seen = []
    def handle(request):
        seen.append(request)
        return response()
    model = LocalModel(settings(model_url="https://provider.example/v1", allow_cloud=True),
                       transport=httpx.MockTransport(handle))
    run(model.chat([{"role": "user", "content": "hi"}]))
    assert seen[0].headers["authorization"] == "Bearer configured-key"


def test_redirects_are_never_followed_and_environment_proxy_is_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://sensitive-proxy.invalid:1234")
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(307, headers={"Location": "https://remote.example/collect"})
    model = LocalModel(settings(), transport=httpx.MockTransport(handle))
    with pytest.raises(ModelUnavailable):
        run(model.chat([{"role": "user", "content": "hi"}]))
    assert len(seen) == 1
    assert seen[0].extensions["timeout"]["read"] == 60
    assert model._client_options()["trust_env"] is False


@pytest.mark.parametrize("body", [[], {}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                                  {"choices": [{"message": {"content": ""}}]}])
def test_malformed_inference_never_reports_success(body):
    model = LocalModel(settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    with pytest.raises(ModelUnavailable):
        run(model.chat([{"role": "user", "content": "hi"}]))


def test_unavailable_transport_returns_safe_failure():
    def handle(request):
        raise httpx.ConnectError("secret-containing backend URL", request=request)
    model = LocalModel(settings(), transport=httpx.MockTransport(handle))
    with pytest.raises(ModelUnavailable, match="unavailable"):
        run(model.chat([{"role": "user", "content": "hi"}]))
    assert run(model.status())["available"] is False


@pytest.mark.parametrize("messages", [[], [{"role": "invalid", "content": "Hi"}],
                                       [{"role": "user", "content": "x" * 32001}],
                                       [{"role": "user", "content": "x"}] * 65,
                                       [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://evil.example"}}]}]])
def test_invalid_or_over_budget_context_never_sends_request(messages):
    def handle(request):
        pytest.fail("Invalid input must not reach the model")
    model = LocalModel(settings(), transport=httpx.MockTransport(handle))
    with pytest.raises(ModelUnavailable):
        run(model.chat(messages))


def test_output_is_capped_even_if_provider_ignores_max_tokens():
    model = LocalModel(settings(max_tokens=16), transport=httpx.MockTransport(lambda request: response("x" * 257)))
    with pytest.raises(ModelUnavailable, match="budget"):
        run(model.chat([{"role": "user", "content": "hi"}]))


def test_usage_is_numeric_bounded_and_not_an_untrusted_log_channel():
    model = LocalModel(settings(), transport=httpx.MockTransport(lambda request: response(
        usage={"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8, "credentials": "secret"})))
    assert run(model.chat([{"role": "user", "content": "hi"}]))["usage"] == {
        "prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8}


def test_vision_builds_bounded_inline_images_without_mutating_history():
    seen = []
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nsmall-test-image").decode()
    history = [{"role": "user", "content": "Describe"}]
    def handle(request):
        seen.append(json.loads(request.content))
        return response("An image")
    model = LocalModel(settings(), transport=httpx.MockTransport(handle))
    assert run(model.chat(history, images=[png]))["content"] == "An image"
    content = seen[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Describe"}
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + png
    assert history == [{"role": "user", "content": "Describe"}]


@pytest.mark.parametrize("images", [["https://example.com/image.png"], ["not-base64"],
                                     [base64.b64encode(b"fake image").decode()],
                                     ["x"] * 5, ["A" * 7_000_000]])
def test_invalid_or_excess_images_are_rejected_before_network(images):
    model = LocalModel(settings(), transport=httpx.MockTransport(lambda request: pytest.fail("network called")))
    with pytest.raises(ModelUnavailable):
        run(model.chat([{"role": "user", "content": "describe"}], images=images))


def test_response_body_limit_and_invalid_json_are_safe_failures():
    for body in (b"X" * 2_000_001, b"invalid private backend response"):
        model = LocalModel(settings(), transport=httpx.MockTransport(
            lambda request, body=body: httpx.Response(200, content=body)))
        with pytest.raises(ModelUnavailable):
            run(model.chat([{"role": "user", "content": "hi"}]))


def test_provider_token_count_cannot_exceed_request_budget():
    model = LocalModel(settings(max_tokens=16), transport=httpx.MockTransport(
        lambda request: response("Small text", usage={"completion_tokens": 17})))
    with pytest.raises(ModelUnavailable, match="budget"):
        run(model.chat([{"role": "user", "content": "hi"}]))


def test_image_declared_type_and_total_size_are_enforced():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * (5_000_000 - 8)).decode()
    model = LocalModel(settings(), transport=httpx.MockTransport(lambda request: pytest.fail("network called")))
    for images in (["data:image/jpeg;base64," + png], [png, png, png]):
        with pytest.raises(ModelUnavailable):
            run(model.chat([{"role": "user", "content": "describe"}], images=images))


def test_missing_cloud_key_never_calls_provider(monkeypatch):
    monkeypatch.delenv("JARVIS_MODEL_API_KEY", raising=False)
    model = LocalModel(settings(model_url="https://provider.example/v1", allow_cloud=True),
                       transport=httpx.MockTransport(lambda request: pytest.fail("network called")))
    with pytest.raises(ModelUnavailable, match="credential"):
        run(model.chat([{"role": "user", "content": "hi"}]))
    assert run(model.status())["available"] is False
