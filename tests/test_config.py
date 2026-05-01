"""Smoke checks for language-config consistency.

Cheaply catches typos when adding new modes: forget a line in one of the three
dicts and the relevant test goes red.
"""

import pytest

from pysar.config import DEFAULT_MODE, MENU_MODES, MODE_LABELS, MODES


def test_modes_and_labels_have_same_keys():
    assert set(MODES.keys()) == set(MODE_LABELS.keys()), (
        "Every code in MODES must have a label in MODE_LABELS and vice versa"
    )


def test_menu_modes_are_subset_of_modes():
    missing = set(MENU_MODES) - set(MODES.keys())
    assert not missing, f"MENU_MODES references unknown modes: {missing}"


def test_default_mode_is_in_menu():
    assert DEFAULT_MODE in MENU_MODES, (
        f"DEFAULT_MODE={DEFAULT_MODE!r} must be present in MENU_MODES"
    )


def test_no_duplicate_menu_modes():
    assert len(MENU_MODES) == len(set(MENU_MODES)), "MENU_MODES contains duplicates"


@pytest.mark.parametrize("code", ["ru", "en", "translate"])
def test_core_modes_exist(code):
    """The three core modes are a documented contract — must always be present."""
    assert code in MODES
    assert code in MODE_LABELS


@pytest.mark.parametrize("mode_code", list(MODES.keys()))
def test_mode_params_have_required_fields(mode_code):
    params = MODES[mode_code]
    assert "language" in params, f"{mode_code}: missing 'language' key"
    assert "translate" in params, f"{mode_code}: missing 'translate' key"
    assert params["translate"] in ("true", "false"), (
        f"{mode_code}: translate must be 'true'/'false', not {params['translate']!r}"
    )
