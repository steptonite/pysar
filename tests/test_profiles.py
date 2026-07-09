"""profiles.py tests — composition, budget, and tolerant import parsing."""

from pysar.profiles import (
    DEFAULT_PROFILES,
    FALLBACK_STYLE_PROMPT,
    PROMPT_TOKEN_BUDGET,
    STYLE_EXAMPLE_INPUT,
    STYLE_PRESETS,
    active_for_language,
    active_set_index,
    budget_usage,
    compose_prompt,
    compose_style_prompt,
    estimate_tokens,
    import_conflicts,
    merge_profiles,
    parse_imported,
    regroup_active,
    remove_profile,
    style_example,
    style_preset,
    upsert_profile,
    validate_profile,
)

_P = [
    {"name": "Dev", "language": "uk", "prompt": "Кажу про Ollama, GitHub, Python."},
    {"name": "Music", "language": "uk", "prompt": "Suno, techno, industrial, мікс."},
    {"name": "English", "language": "en", "prompt": "Natural spoken English."},
]


def test_default_profiles_are_well_formed():
    for p in DEFAULT_PROFILES:
        assert p["name"] and p["language"] and p["prompt"]
        # Craft rule: each default fits the budget on its own.
        assert estimate_tokens(p["prompt"]) < PROMPT_TOKEN_BUDGET


def test_estimate_tokens_monotonic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") >= 1
    assert estimate_tokens("a" * 30) > estimate_tokens("a" * 10)


def test_active_for_language_filters_by_toggle_and_language():
    got = active_for_language(_P, ["Dev", "English"], "uk")
    assert [p["name"] for p in got] == ["Dev"]  # English is en, filtered out


def test_active_for_language_preserves_order():
    got = active_for_language(_P, ["Music", "Dev"], "uk")
    # Order follows the profiles list, not the active_names order.
    assert [p["name"] for p in got] == ["Dev", "Music"]


def test_compose_prompt_concatenates_same_language():
    out = compose_prompt(_P, ["Dev", "Music"], "uk")
    assert "Ollama" in out and "Suno" in out
    assert "English" not in out


def test_compose_prompt_empty_when_none_active():
    assert compose_prompt(_P, [], "uk") == ""


def test_compose_prompt_skips_other_languages():
    out = compose_prompt(_P, ["Dev", "English"], "en")
    assert out == "Natural spoken English."


def test_compose_prompt_respects_token_budget():
    big = [{"name": f"P{i}", "language": "uk", "prompt": "слово " * 200} for i in range(5)]
    names = [p["name"] for p in big]
    out = compose_prompt(big, names, "uk")
    assert estimate_tokens(out) <= PROMPT_TOKEN_BUDGET


def test_budget_usage_reports_requested_total():
    used, budget = budget_usage(_P, ["Dev", "Music"], "uk")
    assert budget == PROMPT_TOKEN_BUDGET
    assert used == estimate_tokens(_P[0]["prompt"]) + estimate_tokens(_P[1]["prompt"])


def test_validate_profile_accepts_clean():
    p = validate_profile({"name": " Dev ", "language": "UK", "prompt": " hi "})
    assert p == {"name": "Dev", "language": "uk", "prompt": "hi", "style_prompt": ""}


def test_validate_profile_rejects_incomplete():
    assert validate_profile({"name": "", "prompt": "x"}) is None
    assert validate_profile({"name": "x", "prompt": ""}) is None
    assert validate_profile("not a dict") is None


def test_validate_profile_unknown_language_falls_back_to_uk():
    p = validate_profile({"name": "x", "language": "klingon", "prompt": "y"})
    assert p["language"] == "uk"


def test_parse_imported_plain_json():
    text = '[{"name":"Dev","language":"uk","prompt":"Ollama, Python"}]'
    profiles, err = parse_imported(text)
    assert err is None
    assert profiles[0]["name"] == "Dev"


def test_parse_imported_tolerates_fences_and_prose():
    text = 'Sure! Here you go:\n```json\n[{"name":"A","language":"en","prompt":"hi"}]\n```\nEnjoy!'
    profiles, err = parse_imported(text)
    assert err is None
    assert profiles[0]["name"] == "A"


def test_parse_imported_repairs_smart_quotes():
    # ChatGPT routinely emits “smart” quotes → invalid JSON. The lenient retry
    # rescues the paste instead of failing the import.
    text = "[{“name”:“Я”,“language”:“uk”,“prompt”:“У ComfyUI я кручу Flux”}]"
    profiles, err = parse_imported(text)
    assert err is None
    assert profiles[0]["name"] == "Я"


def test_parse_imported_repairs_trailing_comma():
    text = '[{"name":"A","language":"uk","prompt":"x"},]'
    profiles, err = parse_imported(text)
    assert err is None
    assert len(profiles) == 1


def test_parse_imported_keeps_legit_curly_quotes_in_content():
    # A paste that's already valid JSON must be parsed verbatim — curly quotes
    # *inside* a string are content, not delimiters, and stay untouched.
    text = '[{"name":"A","language":"uk","prompt":"він сказав “привіт” мені"}]'
    profiles, err = parse_imported(text)
    assert err is None
    assert "“привіт”" in profiles[0]["prompt"]


def test_parse_imported_dedupes_by_name():
    text = '[{"name":"A","language":"uk","prompt":"x"},{"name":"A","language":"uk","prompt":"y"}]'
    profiles, err = parse_imported(text)
    assert err is None
    assert len(profiles) == 1


def test_parse_imported_errors():
    assert parse_imported("")[1] is not None
    assert parse_imported("no json here")[1] is not None
    assert parse_imported("[oops not json]")[1] is not None
    assert parse_imported("[]")[1] is not None  # valid JSON, no usable profiles


def test_merge_profiles_overwrites_same_name_appends_new():
    existing = [{"name": "Dev", "language": "uk", "prompt": "old"}]
    incoming = [
        {"name": "Dev", "language": "uk", "prompt": "new"},
        {"name": "Music", "language": "uk", "prompt": "m"},
    ]
    merged = merge_profiles(existing, incoming)
    assert [p["name"] for p in merged] == ["Dev", "Music"]
    assert merged[0]["prompt"] == "new"  # overwritten


def test_import_conflicts_flags_same_name_same_language_different_content():
    existing = [{"name": "Я", "language": "uk", "prompt": "old calibrated"}]
    incoming = [{"name": "Я", "language": "uk", "prompt": "new"}]
    assert import_conflicts(existing, incoming) == ["Я"]


def test_import_conflicts_allows_same_name_different_language():
    # A "Я" for ru must coexist with the user's already-calibrated uk "Я" —
    # same display name, different profile, not a collision.
    existing = [{"name": "Я", "language": "uk", "prompt": "old calibrated"}]
    incoming = [{"name": "Я", "language": "ru", "prompt": "new"}]
    assert import_conflicts(existing, incoming) == []
    merged = merge_profiles(existing, incoming)
    assert len(merged) == 2
    assert {p["language"] for p in merged if p["name"] == "Я"} == {"uk", "ru"}


def test_import_conflicts_ignores_identical_reimport():
    existing = [{"name": "Dev", "language": "uk", "prompt": "same", "style_prompt": ""}]
    incoming = [{"name": "Dev", "language": "uk", "prompt": "same", "style_prompt": ""}]
    assert import_conflicts(existing, incoming) == []


def test_import_conflicts_ignores_brand_new_names():
    existing = [{"name": "Dev", "language": "uk", "prompt": "x"}]
    incoming = [{"name": "Music", "language": "uk", "prompt": "y"}]
    assert import_conflicts(existing, incoming) == []


def test_validate_profile_accepts_auto_language():
    p = validate_profile({"name": "General", "language": "auto", "prompt": "y"})
    assert p["language"] == "auto"


# ── editor helpers (upsert / remove) ─────────────────────────────────────────


def test_upsert_adds_new_profile():
    profs = [{"name": "Dev", "language": "uk", "prompt": "code"}]
    out, err = upsert_profile(profs, "Music", "uk", "music terms")
    assert err is None
    assert [p["name"] for p in out] == ["Dev", "Music"]
    assert profs == [{"name": "Dev", "language": "uk", "prompt": "code"}]  # input untouched


def test_upsert_rejects_blank_and_duplicate():
    profs = [{"name": "Dev", "language": "uk", "prompt": "code"}]
    _, err = upsert_profile(profs, "", "uk", "x")
    assert err is not None
    _, err = upsert_profile(profs, "Dev", "uk", "again")  # new with taken name
    assert err is not None


def test_upsert_edits_in_place_and_allows_rename():
    profs = [
        {"name": "Dev", "language": "uk", "prompt": "code"},
        {"name": "Music", "language": "uk", "prompt": "m"},
    ]
    out, err = upsert_profile(profs, "Розробка", "uk", "new code", original_name="Dev")
    assert err is None
    assert [p["name"] for p in out] == ["Розробка", "Music"]
    assert out[0]["prompt"] == "new code"


def test_upsert_rename_into_existing_name_is_rejected():
    profs = [
        {"name": "Dev", "language": "uk", "prompt": "code"},
        {"name": "Music", "language": "uk", "prompt": "m"},
    ]
    _, err = upsert_profile(profs, "Music", "uk", "x", original_name="Dev")
    assert err is not None


def test_remove_profile():
    profs = [
        {"name": "Dev", "language": "uk", "prompt": "code"},
        {"name": "Music", "language": "uk", "prompt": "m"},
    ]
    assert [p["name"] for p in remove_profile(profs, "Dev")] == ["Music"]
    assert remove_profile(profs, "Nope") == profs  # absent → unchanged


def test_upsert_allows_same_name_different_language():
    profs = [{"name": "Я", "language": "uk", "prompt": "укр про мене"}]
    out, err = upsert_profile(profs, "Я", "ru", "рус про мене")
    assert err is None
    assert len(out) == 2
    assert {(p["name"], p["language"]) for p in out} == {("Я", "uk"), ("Я", "ru")}


def test_upsert_same_name_same_language_still_rejected():
    profs = [{"name": "Я", "language": "uk", "prompt": "укр"}]
    _, err = upsert_profile(profs, "Я", "uk", "another uk one")
    assert err is not None


def test_remove_profile_by_language_only_removes_that_language():
    profs = [
        {"name": "Я", "language": "uk", "prompt": "укр"},
        {"name": "Я", "language": "ru", "prompt": "рус"},
    ]
    out = remove_profile(profs, "Я", "ru")
    assert [(p["name"], p["language"]) for p in out] == [("Я", "uk")]


# ── regroup_active (profile sets) ────────────────────────────────────────────
def test_regroup_active_buckets_by_language():
    got = regroup_active(_P, ["Dev", "English"])
    assert got == {"uk": ["Dev"], "en": ["English"]}


def test_regroup_active_drops_unknown_members():
    assert regroup_active(_P, ["Dev", "Ghost"]) == {"uk": ["Dev"]}


def test_regroup_active_empty():
    assert regroup_active(_P, []) == {}


def test_regroup_active_disambiguates_duplicate_names_by_dict_member():
    profs = [
        {"name": "Я", "language": "uk", "prompt": "укр"},
        {"name": "Я", "language": "ru", "prompt": "рус"},
    ]
    got = regroup_active(profs, [{"name": "Я", "language": "ru"}])
    assert got == {"ru": ["Я"]}


def test_regroup_active_legacy_bare_name_resolves_to_first_match():
    profs = [
        {"name": "Я", "language": "uk", "prompt": "укр"},
        {"name": "Я", "language": "ru", "prompt": "рус"},
    ]
    got = regroup_active(profs, ["Я"])  # legacy set saved before dict members existed
    assert got == {"uk": ["Я"]}


# ── profile-set persistence normalization (recordings._norm_profile_sets) ────
def test_norm_profile_sets_cleans_and_caps():
    from pysar.config import MAX_PROFILE_SETS
    from pysar.recordings import _norm_profile_sets

    raw = [
        {"name": "  Dev  ", "members": ["A", 1, "B"]},  # trims name, drops non-str member
        {"name": "", "members": []},  # nameless → dropped
        "garbage",  # non-dict → dropped
        {"members": ["X"]},  # no name → dropped
    ]
    out = _norm_profile_sets(raw)
    assert out == [{"name": "Dev", "members": ["A", "B"], "keycode": None, "mods": []}]
    assert _norm_profile_sets(None) == []
    assert len(_norm_profile_sets([{"name": str(i)} for i in range(99)])) == MAX_PROFILE_SETS


def test_norm_profile_sets_keeps_override_binding():
    from pysar.recordings import _norm_profile_sets

    out = _norm_profile_sets([{"name": "Dev", "members": [], "keycode": 18, "mods": ["control"]}])
    assert out == [{"name": "Dev", "members": [], "keycode": 18, "mods": ["control"]}]


def test_set_hotkey_bindings_honor_override():
    from pysar.config import set_hotkey_bindings

    b = set_hotkey_bindings([{"name": "X", "keycode": 99, "mods": ["command"]}, {"name": "Y"}])
    assert b[0]["keycode"] == 99 and b[0]["mods"] == ["command"]  # custom
    assert b[1]["keycode"] == 19  # default ⌃⌥2


# ── active_set_index (which set is currently live) ───────────────────────────
def test_active_set_index_matches_exact_selection():
    sets = [{"name": "S1", "members": ["Dev"]}, {"name": "S2", "members": ["Dev", "English"]}]
    assert active_set_index(sets, _P, {"uk": ["Dev"]}) == 0
    assert active_set_index(sets, _P, {"uk": ["Dev"], "en": ["English"]}) == 1


def test_active_set_index_none_when_handedited():
    sets = [{"name": "S1", "members": ["Dev"]}]
    assert active_set_index(sets, _P, {"uk": ["Dev", "Music"]}) is None
    assert active_set_index(sets, _P, {}) is None


def test_active_set_index_ignores_empty_groups():
    sets = [{"name": "S1", "members": ["Dev"]}]
    assert active_set_index(sets, _P, {"uk": ["Dev"], "en": []}) == 0


def test_compose_prompt_works_for_auto_language():
    profile = {"name": "Gen", "language": "auto", "prompt": "ComfyUI, Claude, render."}
    result = compose_prompt([profile], ["Gen"], "auto")
    assert "ComfyUI" in result


# ── Enhance styles ───────────────────────────────────────────────────────────
def test_style_presets_unique_keys_and_meaning_guard():
    keys = [p["key"] for p in STYLE_PRESETS]
    assert len(keys) == len(set(keys))
    for p in STYLE_PRESETS:
        assert p["name_uk"] and p["name_en"]
        low = p["prompt"].lower()
        assert "збережи" in low or "зберігай" in low, f"{p['key']} must demand meaning preservation"


def test_style_preset_lookup():
    assert style_preset("concise")["key"] == "concise"
    assert style_preset("nonexistent") is None


def test_compose_style_preset_only():
    result = compose_style_prompt([], [], "uk", preset_key="business")
    assert result == style_preset("business")["prompt"]


def test_compose_style_profiles_only():
    profiles = [
        {"name": "p1", "language": "uk", "prompt": "w", "style_prompt": "keep tone"},
        {"name": "p2", "language": "uk", "prompt": "w", "style_prompt": "formal"},
        {"name": "en", "language": "en", "prompt": "w", "style_prompt": "english only"},
    ]
    result = compose_style_prompt(profiles, ["p1", "p2", "en"], "uk")
    assert "keep tone" in result
    assert "formal" in result
    assert "english only" not in result  # wrong language filtered out


def test_compose_style_preset_plus_profiles():
    profiles = [{"name": "p1", "language": "uk", "prompt": "w", "style_prompt": "extra rule"}]
    result = compose_style_prompt(profiles, ["p1"], "uk", preset_key="concise")
    assert result.startswith(style_preset("concise")["prompt"])
    assert "extra rule" in result


def test_compose_style_char_limit_drops_whole_overflow_entry():
    huge = "x" * 3500
    profiles = [
        {"name": "p1", "language": "uk", "prompt": "w", "style_prompt": "keep"},
        {"name": "p2", "language": "uk", "prompt": "w", "style_prompt": huge},
    ]
    result = compose_style_prompt(profiles, ["p1", "p2"], "uk")
    assert "keep" in result
    assert huge not in result


def test_compose_style_empty_falls_back():
    assert compose_style_prompt([], [], "uk") == FALLBACK_STYLE_PROMPT


def test_validate_profile_keeps_style_prompt():
    p = validate_profile({"name": "t", "language": "uk", "prompt": "w", "style_prompt": " x "})
    assert p["style_prompt"] == "x"


def test_upsert_round_trips_style_prompt():
    out, err = upsert_profile([], "t", "uk", "whisper prompt", style_prompt="formal")
    assert err is None
    assert out[0]["style_prompt"] == "formal"


def test_style_example_for_custom_business_emoji():
    for key in (None, "custom", "business", "emoji"):
        ex = style_example(key)
        assert ex is not None
        raw, rewritten = ex
        assert raw == STYLE_EXAMPLE_INPUT
        assert rewritten and rewritten != raw


def test_style_example_absent_for_other_presets():
    assert style_example("bullets") is None
    assert style_example("nonexistent") is None
