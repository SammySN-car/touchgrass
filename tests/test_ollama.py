import pytest

import app.ollama_client as oc


class FakeResponse:
    """Duck-typed stand-in for requests.Response: no network."""

    def __init__(self, payload=None, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


def test_mock_mode_true_when_env_set(monkeypatch):
    monkeypatch.setenv("GRASS_MOCK", "1")
    assert oc.mock_mode() is True


def test_mock_mode_false_when_unset(monkeypatch):
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    assert oc.mock_mode() is False


def test_mock_mode_read_at_call_time(monkeypatch):
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    assert oc.mock_mode() is False  # imported module already; env read now
    monkeypatch.setenv("GRASS_MOCK", "1")
    assert oc.mock_mode() is True


def test_fallback_order_first_fails_second_succeeds(monkeypatch):
    attempted = []

    def fake_post(url, json=None, timeout=None):
        model = json["model"]
        attempted.append(model)
        if model == "gemma3:4b":
            return FakeResponse(status_error=RuntimeError("HTTP 500"))
        return FakeResponse({"message": {"content": '{"flavor": "ok"}'}})

    monkeypatch.setattr(oc.requests, "post", fake_post)
    result = oc.chat_json("gemma3:4b", "system prompt", "user prompt")
    assert result == {"flavor": "ok"}
    assert attempted == ["gemma3:4b", "llama3.2"]


def test_request_body_shape(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        captured["timeout"] = timeout
        return FakeResponse({"message": {"content": "{}"}})

    monkeypatch.setattr(oc.requests, "post", fake_post)
    oc.chat_json("gemma3:4b", "SYS", "USR", timeout=7)
    assert captured["url"] == oc.OLLAMA_URL + "/api/chat"
    assert captured["timeout"] == 7
    body = captured["body"]
    assert body["model"] == "gemma3:4b"
    assert body["stream"] is False
    assert body["keep_alive"] == -1
    assert body["options"] == {"temperature": 0.75}
    assert body["format"] == "json"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_all_models_fail_raises_runtimeerror(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return FakeResponse(status_error=RuntimeError("connection refused"))

    monkeypatch.setattr(oc.requests, "post", fake_post)

    with pytest.raises(RuntimeError) as exc:
        oc.chat_json("gemma3:4b", "s", "u")
    msg = str(exc.value)
    for m in ("gemma3:4b", "llama3.2", "qwen2.5:7b"):
        assert f"{m}: connection refused" in msg
    assert msg.count(" | ") == 2  # three segments joined by " | "


def test_all_fail_error_truncated_to_120_chars(monkeypatch):
    long_error = "x" * 500

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(status_error=RuntimeError(long_error))

    monkeypatch.setattr(oc.requests, "post", fake_post)

    with pytest.raises(RuntimeError) as exc:
        oc.chat_json("gemma3:4b", "s", "u")
    assert "x" * 120 in str(exc.value)
    assert "x" * 121 not in str(exc.value)


def test_reachable_false_when_get_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr(oc.requests, "get", boom)
    assert oc.reachable() is False


def test_reachable_true_on_success(monkeypatch):
    monkeypatch.setattr(oc.requests, "get", lambda *a, **k: FakeResponse())
    assert oc.reachable() is True
