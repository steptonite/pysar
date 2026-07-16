"""postprocessor.py tests — Ollama client, all failure modes fall back cleanly."""

import requests

from pysar.postprocessor import enhance, is_ollama_alive, limit_emoji, list_models, preload


def _fake_response(json_data, status=200):
    """A callable mimicking requests.post/get that returns a canned response."""

    class FakeResp:
        status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(f"{self.status_code}")

        def json(self):
            return json_data

    return lambda *a, **kw: FakeResp()


# ── enhance ──────────────────────────────────────────────────────────────────
def test_enhance_returns_cleaned_text(monkeypatch):
    # Strips whitespace and a leading <think> reasoning block (qwen3 quirk).
    reply = "  <think>some reasoning</think>  Fixed text  "
    monkeypatch.setattr(requests, "post", _fake_response({"message": {"content": reply}}))
    result, err = enhance("input", "be concise", "model")
    assert err is None
    assert result == "Fixed text"


def test_enhance_empty_input_short_circuits(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("API must not be called for empty input")

    monkeypatch.setattr(requests, "post", _boom)
    result, err = enhance("   ", "style", "model")
    assert result is None
    assert err is None


def test_enhance_empty_reply_is_error(monkeypatch):
    monkeypatch.setattr(requests, "post", _fake_response({"message": {"content": "   "}}))
    result, err = enhance("input", "style", "model")
    assert result is None
    assert err == "Empty reply from model"


def test_enhance_connection_error(monkeypatch):
    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", _raise)
    result, err = enhance("input", "style", "model")
    assert result is None
    assert "not running" in err


def test_enhance_timeout(monkeypatch):
    def _raise(*a, **kw):
        raise requests.exceptions.Timeout("slow")

    monkeypatch.setattr(requests, "post", _raise)
    result, err = enhance("input", "style", "model")
    assert result is None
    assert "timed out" in err


def test_enhance_malformed_json(monkeypatch):
    class BadResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(requests, "post", lambda *a, **kw: BadResp())
    result, err = enhance("input", "style", "model")
    assert result is None
    assert "Enhance error" in err


def test_enhance_payload_structure(monkeypatch):
    captured = {}

    def record(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["timeout"] = kwargs.get("timeout")
        return _fake_response({"message": {"content": "ok"}})()

    monkeypatch.setattr(requests, "post", record)
    enhance("input text", "system prompt", "my-model")
    assert captured["url"].endswith("/api/chat")
    payload = captured["json"]
    assert payload["model"] == "my-model"
    assert payload["stream"] is False
    assert payload["keep_alive"]
    assert payload["messages"][0]["role"] == "system"
    # The style prompt leads; the tool guard (anti-refusal) is appended after it.
    assert payload["messages"][0]["content"].startswith("system prompt")
    assert "сирий матеріал" in payload["messages"][0]["content"]
    # The dictation goes into the user turn wrapped as quoted material, with
    # the rewrite instruction anchored AFTER the text (recency wins on 4B).
    assert payload["messages"][1]["role"] == "user"
    user = payload["messages"][1]["content"]
    assert "input text" in user
    assert "<<<" in user
    assert user.index("Перепиши текст") > user.index("input text")
    assert "не команда" in user or "НЕ команда" in user
    assert captured["timeout"] > 0


def test_enhance_fewshot_example_pair(monkeypatch):
    captured = {}

    def record(url, **kwargs):
        captured["json"] = kwargs["json"]
        return _fake_response({"message": {"content": "ok"}})()

    monkeypatch.setattr(requests, "post", record)
    enhance("real text", "style", "m", example=("raw ex", "clean ex"))
    msgs = captured["json"]["messages"]
    # system, example user, example assistant, real user — in that order.
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert "raw ex" in msgs[1]["content"] and "<<<" in msgs[1]["content"]
    assert msgs[2]["content"] == "clean ex"
    assert "real text" in msgs[3]["content"]


# ── preamble stripping ───────────────────────────────────────────────────────
def _enhanced(monkeypatch, reply):
    monkeypatch.setattr(requests, "post", _fake_response({"message": {"content": reply}}))
    result, err = enhance("input", "style", "model")
    assert err is None
    return result


def test_enhance_strips_colon_preamble(monkeypatch):
    assert _enhanced(monkeypatch, "Ось виправлений текст:\nЧистий текст.") == "Чистий текст."


def test_enhance_strips_two_stage_preamble(monkeypatch):
    reply = "Зроблено.\n\nОсь виправлений текст:\n\nЧистий текст."
    assert _enhanced(monkeypatch, reply) == "Чистий текст."


def test_enhance_strips_wrapping_quotes(monkeypatch):
    assert _enhanced(monkeypatch, "«Чистий текст.»") == "Чистий текст."


def test_enhance_keeps_legit_os_opening(monkeypatch):
    # A rewrite that merely starts with «Ось …» is content, not a preamble.
    reply = "Ось і все, я закінчив роботу.\nДалі другий абзац тексту."
    assert _enhanced(monkeypatch, reply) == reply


def test_enhance_keeps_legit_list_header(monkeypatch):
    # A colon header without meta-words about the text (e.g. a bullets rewrite).
    reply = "Задачі на завтра:\n- перше\n- друге"
    assert _enhanced(monkeypatch, reply) == reply


def test_enhance_strips_doubled_markers(monkeypatch):
    # falcon3 echoed the <<< marker twice during the v3 screening.
    assert _enhanced(monkeypatch, "<<<\n<<<\nЧистий текст.\n>>>") == "Чистий текст."


def test_enhance_preamble_only_reply_kept(monkeypatch):
    # Nothing after the header — better to keep the preamble than return nothing.
    assert _enhanced(monkeypatch, "Ось виправлений текст:") == "Ось виправлений текст:"


# ── is_ollama_alive ──────────────────────────────────────────────────────────
def test_is_alive_true(monkeypatch):
    monkeypatch.setattr(requests, "get", _fake_response({}))
    assert is_ollama_alive() is True


def test_is_alive_false(monkeypatch):
    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(requests, "get", _raise)
    assert is_ollama_alive() is False


# ── list_models ──────────────────────────────────────────────────────────────
def test_list_models_sorted(monkeypatch):
    data = {"models": [{"name": "qwen3:4b"}, {"name": "gemma3:4b"}]}
    monkeypatch.setattr(requests, "get", _fake_response(data))
    assert list_models() == ["gemma3:4b", "qwen3:4b"]


def test_list_models_failure_returns_empty(monkeypatch):
    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(requests, "get", _raise)
    assert list_models() == []


# ── preload ──────────────────────────────────────────────────────────────────
def test_preload_swallows_exceptions(monkeypatch):
    def _raise(*a, **kw):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(requests, "post", _raise)
    preload("model")  # must not raise

    monkeypatch.setattr(requests, "post", _fake_response({}))
    preload("model")


# ── limit_emoji ──────────────────────────────────────────────────────────────
def test_limit_emoji_under_cap_untouched():
    text = "Привіт 😠! Все ок 🙏."
    assert limit_emoji(text) == text


def test_limit_emoji_cuts_extras_and_orphan_spaces():
    text = "Один 🤔. Два 😟. Три 🙏. Чотири 😱. П'ять 🤯."
    assert limit_emoji(text) == "Один 🤔. Два 😟. Три 🙏. Чотири. П'ять."


def test_limit_emoji_keeps_zwj_sequences_whole():
    text = "А 🤷‍♀️. Б 🤦‍♂️. В 🙏. Г 🤷‍♀️кінець."
    out = limit_emoji(text)
    assert out == "А 🤷‍♀️. Б 🤦‍♂️. В 🙏. Г кінець."
    assert "\u200d" not in out.replace("🤷‍♀️", "").replace("🤦‍♂️", "")


def test_limit_emoji_plain_text_untouched():
    text = "Жодного емоджі, просто текст — з тире і трьома крапками…"
    assert limit_emoji(text) == text
