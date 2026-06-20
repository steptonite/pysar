"""Guards for the UI string tables: the two languages must stay in lockstep
(same keys), interpolation must work, and lookups must never blank out."""

from pysar.i18n import UI_LANGS, strings, t


def test_languages_have_identical_key_sets():
    # A key present in one language but missing in the other would silently fall
    # back to uk at runtime — catch the drift here instead.
    uk, en = strings("uk"), strings("en")
    assert set(uk) == set(en)


def test_every_ui_lang_resolves():
    for lang in UI_LANGS:
        assert strings(lang)


def test_t_interpolates_named_fields():
    assert t("en", "st.keepLast", n=5) == "Keeping last 5 recordings"
    assert t("uk", "tray.hotkey", label="Caps Lock") == "Хоткей: Caps Lock"


def test_t_unknown_lang_falls_back_to_uk():
    assert t("fr", "sec.audio") == strings("uk")["sec.audio"]


def test_t_unknown_key_returns_the_key():
    assert t("uk", "no.such.key") == "no.such.key"


def test_no_interpolation_when_no_kwargs():
    # A template with braces but no kwargs is returned verbatim (no format crash).
    assert "{label}" in t("en", "st.hotkeySet")
