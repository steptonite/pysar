"""profiles.py tests — composition, budget, and tolerant import parsing."""

from pysar.profiles import (
    DEFAULT_PROFILES,
    PROMPT_TOKEN_BUDGET,
    active_for_language,
    budget_usage,
    compose_prompt,
    estimate_tokens,
    merge_profiles,
    parse_imported,
    remove_profile,
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
    assert p == {"name": "Dev", "language": "uk", "prompt": "hi"}


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
