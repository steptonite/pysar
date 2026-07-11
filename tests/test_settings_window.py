"""Tests for the testable core of the settings window — HTML rendering and the
JS→Python message router. The AppKit/WebKit glue is not exercised here."""

import json
import re

from pysar.backend.settings_window import build_html, dispatch


def _state(**over):
    base = {
        "mics": ["Built-in", "USB Mic"],
        "current_mic": None,
        "save_recordings": False,
        "keep_last": 10,
        "keep_last_options": [5, 10, 20],
        "launch_at_login": False,
        "hotkey_label": "Caps Lock",
        "recordings_dir": "/tmp/recs",
    }
    base.update(over)
    return base


# ── dispatch ──────────────────────────────────────────────────────────────────


def test_dispatch_calls_handler_with_value():
    seen = []
    dispatch({"action": "set_keep", "value": 20}, {"set_keep": seen.append})
    assert seen == [20]


def test_dispatch_passes_none_value_through():
    # "" → null in JS → None here means "system default microphone".
    seen = []
    dispatch({"action": "set_mic", "value": None}, {"set_mic": seen.append})
    assert seen == [None]


def test_dispatch_valueless_action_calls_with_no_args():
    calls = []
    dispatch({"action": "open_folder"}, {"open_folder": lambda: calls.append(1)})
    assert calls == [1]


def test_dispatch_unknown_action_is_ignored():
    # A stale front-end must never crash the back-end.
    dispatch({"action": "nope", "value": 1}, {"set_save": lambda v: None})


def test_dispatch_missing_action_key_is_ignored():
    dispatch({"value": 1}, {"set_save": lambda v: None})


# ── build_html ────────────────────────────────────────────────────────────────


def test_build_html_embeds_state_as_json():
    html = build_html(_state(keep_last=5))
    m = re.search(r"let STATE = (\{.*?\});", html, re.DOTALL)
    assert m, "STATE assignment not found"
    parsed = json.loads(m.group(1))
    assert parsed["keep_last"] == 5
    assert parsed["mics"] == ["Built-in", "USB Mic"]


def test_build_html_has_all_control_ids():
    html = build_html(_state())
    for cid in ("mic", "save", "keep", "login", "open-folder", "hk-toggle", "rec-path"):
        assert f'id="{cid}"' in html


def test_build_html_escapes_angle_brackets_in_state():
    # A device name with "<" must not break out of the <script> block.
    html = build_html(_state(mics=["</script><b>x"]))
    assert "</script><b>" not in html.split("let STATE")[1].split(";")[0]
    assert "\\u003c" in html


def test_build_html_placeholder_is_consumed():
    html = build_html(_state())
    assert "/*__STATE__*/null" not in html


# ── Enhance screen ────────────────────────────────────────────────────────────


def test_build_html_has_enhance_screen_and_controls():
    html = build_html(_state())
    assert 'id="screen-enhance"' in html
    for cid in ("go-enhance", "enh-enabled", "enh-style", "enh-model", "enh-status", "back-enh"):
        assert f'id="{cid}"' in html


def test_build_html_embeds_enhance_state():
    html = build_html(
        _state(
            enhance_enabled=True,
            enhance_model="qwen3:4b",
            enhance_style="concise",
            enhance_styles=[{"key": "concise", "name_uk": "Коротше", "name_en": "Concise"}],
            enhance_status={"alive": True, "models": ["qwen3:4b"]},
        )
    )
    m = re.search(r"let STATE = (\{.*?\});", html, re.DOTALL)
    parsed = json.loads(m.group(1))
    assert parsed["enhance_enabled"] is True
    assert parsed["enhance_style"] == "concise"
    assert parsed["enhance_status"]["models"] == ["qwen3:4b"]


def test_dispatch_enhance_actions_route():
    seen = []
    handlers = {
        "set_enhance_enabled": lambda v: seen.append(("enabled", v)),
        "set_enhance_model": lambda v: seen.append(("model", v)),
        "set_enhance_style": lambda v: seen.append(("style", v)),
    }
    dispatch({"action": "set_enhance_enabled", "value": True}, handlers)
    dispatch({"action": "set_enhance_model", "value": "qwen3:4b"}, handlers)
    dispatch({"action": "set_enhance_style", "value": "bullets"}, handlers)
    assert seen == [("enabled", True), ("model", "qwen3:4b"), ("style", "bullets")]


# ── File-transcription screen ─────────────────────────────────────────────────


def test_build_html_has_ft_screen_and_controls():
    html = build_html(_state())
    assert 'id="screen-ft"' in html
    for cid in ("go-ft", "ft-lang", "ft-pick", "ft-bar", "ft-status", "ft-cancel",
                "ft-reveal", "back-ft", "ft-prompt-src", "ft-prompt", "ft-meter",
                "ft-count", "ft-example", "ft-need"):
        assert f'id="{cid}"' in html


def test_dispatch_ft_actions_route():
    seen = []
    handlers = {
        "ft_pick_file": lambda: seen.append(("pick",)),
        "set_ft_lang": lambda v: seen.append(("lang", v)),
        "ft_cancel": lambda: seen.append(("cancel",)),
        "ft_open_result": lambda: seen.append(("open",)),
    }
    dispatch({"action": "ft_pick_file"}, handlers)
    dispatch({"action": "set_ft_lang", "value": "uk"}, handlers)
    dispatch({"action": "ft_cancel"}, handlers)
    dispatch({"action": "ft_open_result"}, handlers)
    assert seen == [("pick",), ("lang", "uk"), ("cancel",), ("open",)]


def test_dispatch_ft_prompt_actions_route():
    seen = []
    handlers = {
        "set_ft_prompt": lambda v: seen.append(("prompt", v)),
        "set_ft_prompt_source": lambda v: seen.append(("src", v)),
    }
    dispatch({"action": "set_ft_prompt", "value": "Claude, MCP"}, handlers)
    dispatch({"action": "set_ft_prompt_source", "value": "custom"}, handlers)
    assert seen == [("prompt", "Claude, MCP"), ("src", "custom")]
