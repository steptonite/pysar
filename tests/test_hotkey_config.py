"""Pure-logic checks for the user-assignable hotkeys.

Covers the binding rule (is_bindable), the human label (binding_label) and the
key-down matcher (match_keydown) — the parts that decide, with no AppKit/Quartz,
whether a captured key is allowed and which action it fires. The CGEventTap glue
in the backend isn't exercised here.
"""

from pysar.config import (
    DEFAULT_HOTKEY,
    DEFAULT_LANG_HOTKEYS,
    LANG_HOTKEY_ACTIONS,
    binding_label,
    is_bindable,
    match_keydown,
)


# ── is_bindable ─────────────────────────────────────────────────────────────
def test_bare_printable_key_is_rejected():
    # "D" alone would type a "d" (listen-only tap can't swallow it).
    assert not is_bindable(2, [])


def test_printable_key_with_modifier_is_allowed():
    assert is_bindable(2, ["control", "option"])


def test_bare_nontyping_keys_are_allowed():
    assert is_bindable(57, [])  # Caps Lock
    assert is_bindable(54, [])  # Right ⌘
    assert is_bindable(61, [])  # Right ⌥
    assert is_bindable(105, [])  # F13


# ── binding_label ───────────────────────────────────────────────────────────
def test_label_combo_uses_apple_order_and_glyphs():
    # control, option, shift, command order regardless of input order.
    assert binding_label(32, ["command", "control"]) == "⌃⌘U"
    assert binding_label(14, ["control", "option"]) == "⌃⌥E"


def test_label_bare_special_keys_are_spelled_out():
    assert binding_label(57, []) == "Caps Lock"
    assert binding_label(61, []) == "Right ⌥"


def test_label_unknown_keycode_falls_back():
    assert binding_label(999, ["option"]) == "⌥Key 999"


# ── match_keydown ───────────────────────────────────────────────────────────
def _bindings():
    return [
        {"action": "__toggle__", "keycode": 49, "mods": ["control"]},
        {"action": "uk", "keycode": 32, "mods": ["control", "option"]},
    ]


def test_match_requires_exact_modifier_set():
    assert match_keydown(_bindings(), 32, ["control", "option"]) == "uk"
    # An extra modifier must NOT match — avoids ⌃⌥⌘U firing the ⌃⌥U binding.
    assert match_keydown(_bindings(), 32, ["control", "option", "command"]) is None


def test_match_returns_none_for_unbound_key():
    assert match_keydown(_bindings(), 3, ["control"]) is None


def test_match_toggle_action():
    assert match_keydown(_bindings(), 49, ["control"]) == "__toggle__"


# ── defaults ────────────────────────────────────────────────────────────────
def test_defaults_are_self_consistent():
    assert is_bindable(DEFAULT_HOTKEY["keycode"], DEFAULT_HOTKEY["mods"])
    # Every language is an assignable slot; one binding each, in menu order.
    actions = [h["action"] for h in DEFAULT_LANG_HOTKEYS]
    assert actions == LANG_HOTKEY_ACTIONS
    # Assigned defaults must be valid; the rest start unassigned (keycode None).
    for h in DEFAULT_LANG_HOTKEYS:
        if h["keycode"] is None:
            assert h["mods"] == []
        else:
            assert is_bindable(h["keycode"], h["mods"])


def test_only_a_few_languages_have_a_default_binding():
    assigned = [h["action"] for h in DEFAULT_LANG_HOTKEYS if h["keycode"] is not None]
    assert set(assigned) == {"uk", "ru", "translate"}


# ── profile-set hotkeys (⌃⌥<digit>) ──────────────────────────────────────────
def test_set_hotkey_bindings_indexed_digits():
    from pysar.config import set_hotkey_bindings

    b = set_hotkey_bindings([{"name": "Dev"}, {"name": "VFX"}])
    assert [x["action"] for x in b] == ["set:0", "set:1"]
    assert b[0]["keycode"] == 18 and b[0]["mods"] == ["control", "option"]  # ⌃⌥1
    assert b[1]["keycode"] == 19  # ⌃⌥2


def test_set_hotkey_bindings_capped_at_nine():
    from pysar.config import MAX_PROFILE_SETS, set_hotkey_bindings

    assert len(set_hotkey_bindings([{"name": str(i)} for i in range(20)])) == MAX_PROFILE_SETS


def test_set_hotkey_label():
    from pysar.config import set_hotkey_label

    assert set_hotkey_label(0) == "⌃⌥1"
    assert set_hotkey_label(8) == "⌃⌥9"
    assert set_hotkey_label(99) == ""
