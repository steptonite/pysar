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
    for cid in (
        "go-ft",
        "ft-lang",
        "ft-pick",
        "ft-bar",
        "ft-qstatus",
        "ft-qlist",
        "ft-pause",
        "ft-cancel-all",
        "back-ft",
        "ft-prompt-src",
        "ft-prompt",
        "ft-meter",
        "ft-count",
        "ft-example",
        "ft-need",
    ):
        assert f'id="{cid}"' in html


def test_dispatch_ft_actions_route():
    seen = []
    handlers = {
        "ft_pick_files": lambda: seen.append(("pick",)),
        "set_ft_lang": lambda v: seen.append(("lang", v)),
        "ft_pause": lambda: seen.append(("pause",)),
        "ft_cancel_all": lambda: seen.append(("cancel_all",)),
        "ft_remove": lambda v: seen.append(("remove", v)),
        "ft_open_result": lambda v: seen.append(("open", v)),
    }
    dispatch({"action": "ft_pick_files"}, handlers)
    dispatch({"action": "set_ft_lang", "value": "uk"}, handlers)
    dispatch({"action": "ft_pause"}, handlers)
    dispatch({"action": "ft_cancel_all"}, handlers)
    dispatch({"action": "ft_remove", "value": 2}, handlers)
    dispatch({"action": "ft_open_result", "value": 1}, handlers)
    assert seen == [
        ("pick",),
        ("lang", "uk"),
        ("pause",),
        ("cancel_all",),
        ("remove", 2),
        ("open", 1),
    ]


def test_dispatch_ft_prompt_actions_route():
    seen = []
    handlers = {
        "set_ft_prompt": lambda v: seen.append(("prompt", v)),
        "set_ft_prompt_source": lambda v: seen.append(("src", v)),
    }
    dispatch({"action": "set_ft_prompt", "value": "Claude, MCP"}, handlers)
    dispatch({"action": "set_ft_prompt_source", "value": "custom"}, handlers)
    assert seen == [("prompt", "Claude, MCP"), ("src", "custom")]


# ── Meeting-recording rotation (24.08.2026) ───────────────────────────────────


def test_build_html_has_the_meeting_keep_picker():
    html = build_html(_state())
    assert 'id="mt-keep"' in html


def test_build_html_embeds_the_meeting_keep_state():
    html = build_html(_state(meeting_keep_last=0, meeting_keep_options=[5, 10, 0]))
    m = re.search(r"let STATE = (\{.*?\});", html, re.DOTALL)
    parsed = json.loads(m.group(1))
    # 0 = "keep everything"; it must reach the window as an option, not be
    # filtered out as falsy on the way.
    assert parsed["meeting_keep_last"] == 0
    assert 0 in parsed["meeting_keep_options"]


def test_dispatch_routes_the_meeting_keep_choice():
    seen = []
    dispatch({"action": "set_meeting_keep", "value": 50}, {"set_meeting_keep": seen.append})
    assert seen == [50]


# ── Meeting AUDIO folder button (24.08.2026) ───────────────────────────────────
# Distinct from the transcripts folder: "Save transcript to file" writes
# Markdown text, this opens the raw mic/system-audio WAV buffer (meetings/)
# that "Keep meeting recordings" actually rotates. Without it, the only way to
# find the audio was to know the path by heart.


def test_build_html_has_the_meeting_audio_folder_button():
    html = build_html(_state())
    assert 'id="mt-audio-open"' in html
    assert 'id="mt-audio-path"' in html


def test_build_html_embeds_the_meetings_dir_state():
    html = build_html(_state(meetings_dir="/tmp/meetings"))
    m = re.search(r"let STATE = (\{.*?\});", html, re.DOTALL)
    parsed = json.loads(m.group(1))
    assert parsed["meetings_dir"] == "/tmp/meetings"


def test_dispatch_routes_open_meetings_folder():
    calls = []
    dispatch({"action": "open_meetings_folder"}, {"open_meetings_folder": lambda: calls.append(1)})
    assert calls == [1]


# ── Розділення спікерів (діаризація після Стоп, 06.09.2026) ──────────────────


def test_build_html_has_the_diarization_controls():
    """Розділення живе В ТОМУ САМОМУ списку, що й розмежування каналів.

    06.09.2026 Льоша: «нахуя плодити сущності, якщо можна додати у випадаючий
    список». Два керма на одне питання «хто говорить» — це і є плодження:
    людина не розуміє, що з чим поєднувати. Тому окремого перемикача більше
    немає, а тест стежить, щоб він не повернувся."""
    html = build_html(_state())
    for cid in (
        "mt-source",
        "mt-diar-install",
        "mt-diar-status",
        "ft-diar",
        "ft-diar-install",
        "ft-diar-status",
    ):
        assert f'id="{cid}"' in html
    assert 'type="checkbox" id="mt-diar"' not in html, "окремий перемикач повернувся"
    assert "meeting.source.split" in html or "set_meeting_diarize" in html


def test_diarization_state_survives_a_push():
    """Стан докачки оновлюється пушем із фонового потоку — якщо renderDiar не
    у списку pysarApply, прогрес завмирає на «Починаю…» назавжди."""
    html = build_html(_state())
    assert "window.renderDiar" in html
    assert "if (window.renderDiar) window.renderDiar();" in html


def test_dispatch_routes_the_diarization_actions():
    seen = {}
    handlers = {
        "set_meeting_diarize": lambda v: seen.setdefault("meeting", v),
        "set_ft_diarize": lambda v: seen.setdefault("ft", v),
        "diar_install": lambda: seen.setdefault("install", True),
    }
    dispatch({"action": "set_meeting_diarize", "value": True}, handlers)
    dispatch({"action": "set_ft_diarize", "value": False}, handlers)
    dispatch({"action": "diar_install"}, handlers)
    assert seen == {"meeting": True, "ft": False, "install": True}


def test_build_html_embeds_the_diarization_state():
    html = build_html(
        _state(
            meeting_diarize=True,
            diar_status={"engine": False, "models": False, "ready": False, "download_mb": 110},
            diar_busy=False,
        )
    )
    assert '"meeting_diarize": true' in html or '"meeting_diarize":true' in html


# ── Регресія 06.09.2026: список, який не перемикається ────────────────────────
# renderDiar() перечитує STATE і ПЕРЕЗАПИСУЄ .value кожного з цих селектів.
# Бек на ці дії стан назад не пушить, тож якщо обробник change не оновить STATE
# сам — вибір людини відкочується тієї ж миті, і список виглядає захардкоденим.
# Саме це Льоша й побачив: «воно не перемикається ніяк».
# Поведінку перевірено ще й у живому браузері на зібраній сторінці; тут —
# структурний запобіжник, щоб рядок не зник при наступній правці.


def _handler_body(html: str, marker: str) -> str:
    start = html.index(marker)
    return html[start : html.index("});", start)]


def test_meeting_source_change_updates_state_before_sending():
    body = _handler_body(build_html(_state()), 'mtSource.addEventListener("change"')
    assert "STATE.meeting_source_mode =" in body
    assert "STATE.meeting_diarize =" in body


def test_ft_diar_change_updates_state_before_sending():
    body = _handler_body(build_html(_state()), 'ftDiar.addEventListener("change"')
    assert "STATE.ft_diarize = on;" in body


def test_speaker_count_control_is_on_both_screens():
    html = build_html(_state(diar_speakers=3))
    assert '<select id="mt-spk">' in html
    assert '<select id="ft-spk">' in html
    assert 'send("set_diar_speakers"' in html
    assert '"diar_speakers": 3' in html or '"diar_speakers":3' in html


def test_dispatch_routes_the_speaker_count():
    seen = []
    dispatch({"action": "set_diar_speakers", "value": 4}, {"set_diar_speakers": seen.append})
    assert seen == [4]


# ── Термо-сторож ──────────────────────────────────────────────────────────────


def test_thermal_control_is_on_the_file_screen():
    html = build_html(_state(thermal_mode="gentle"))
    assert '<select id="ft-thermal">' in html
    assert 'send("set_thermal_mode"' in html
    assert '"thermal_mode": "gentle"' in html or '"thermal_mode":"gentle"' in html


def test_thermal_change_updates_state_before_sending():
    # Той самий клас баги, що 06.09.2026: якщо STATE не оновити ДО send(),
    # наступний перемальовок відкотить вибір і список виглядатиме мертвим.
    body = _handler_body(build_html(_state()), 'ftTh.addEventListener("change"')
    assert body.index("STATE.thermal_mode") < body.index('send("set_thermal_mode"')


def test_dispatch_routes_the_thermal_mode():
    seen = []
    dispatch({"action": "set_thermal_mode", "value": "gentle"}, {"set_thermal_mode": seen.append})
    assert seen == ["gentle"]


def test_cooling_phase_has_its_own_line_in_the_queue_status():
    # Завмерла шкала без підпису читається як зависання застосунку.
    html = build_html(_state())
    assert "ft.cooling" in html
    assert 'ph.startsWith("cool")' in html
