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
