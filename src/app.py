"""Entry point. Voice-typing business logic, free of platform-specific code.

Event flow:
  Caps Lock (tap 1) → start recording
  Caps Lock (tap 2) → stop → Whisper → clipboard → Cmd+V → restore clipboard

Platform-specific adapters (hotkey, paste, tray) live in pysar.backend.
"""

import contextlib
import queue
import sys
import threading
import time
from datetime import datetime

from . import postprocessor, server
from .backend import HotkeyListener, Paster, TranscriptWindow, Tray, login_item_enabled
from .config import (
    DEFAULT_MODE,
    IDLE_ICON_FALLBACK,
    LANG_SEED,
    MAX_PROFILE_SETS,
    MENU_MODES,
    MODE_ICONS,
    MODE_LABELS,
    MODES,
    binding_label,
    is_bindable,
    set_hotkey_bindings,
)
from .i18n import t
from .profiles import (
    STYLE_PRESETS,
    compose_prompt,
    compose_style_prompt,
    import_conflicts,
    merge_profiles,
    parse_imported,
    regroup_active,
    remove_profile,
    style_example,
    upsert_profile,
)
from .recorder import AudioRecorder, list_input_devices
from .recordings import (
    KEEP_LAST_OPTIONS,
    load_settings,
    recordings_dir,
    save_recording,
    save_settings,
)
from .syscap import SystemAudioRecorder
from .transcriber import is_alive, transcribe
from .transcripts import TranscriptFile


class VoiceTyper:
    def __init__(self):
        # Persisted settings (off by default — audio stays in memory).
        self._settings = load_settings()

        # Restore the last-used language; fall back if the stored code is unknown.
        saved_mode = self._settings.get("mode", DEFAULT_MODE)
        self._mode = saved_mode if saved_mode in MODES else DEFAULT_MODE
        # UI language for status-line strings (kept in sync via _on_set_lang).
        self._ui_lang = self._settings.get("ui_lang", "uk")

        self._recorder = AudioRecorder(device=self._settings.get("mic"))
        self._paster = Paster()
        self._recording = False
        self._busy = False  # blocks re-entry while a transcription is in flight
        self._streaming = False  # this session uses the streaming path
        # Streaming session state (set up per session in _start_streaming).
        self._seg_queue: queue.Queue | None = None
        self._seg_worker: threading.Thread | None = None
        self._first_typed = False
        self._stream_err: str | None = None
        self._typed_chars = 0

        # "Transcribe everything": system audio + mic → live transcript window +
        # file. A separate on/off capture, independent of the dictation hotkey,
        # built lazily on first use so the normal dictation path pays nothing.
        self._meeting = False
        self._meeting_mic = False  # is the running capture holding the mic?
        self._meeting_stopping = False  # drain in progress — no new capture yet
        self._sysrec: SystemAudioRecorder | None = None
        self._transcript_window: TranscriptWindow | None = None
        self._transcript_file: TranscriptFile | None = None
        self._meeting_queue: queue.Queue | None = None
        self._meeting_worker: threading.Thread | None = None
        self._meeting_tails: dict[str | None, str] = {}  # per-source rolling context
        self._meeting_server_down = False

        self._tray = Tray(
            modes=[(code, MODE_LABELS[code]) for code in MENU_MODES],
            current_mode=self._mode,
            on_mode_select=self._on_mode_select,
            save_recordings=self._settings["save_recordings"],
            keep_last=self._settings["keep_last"],
            keep_last_options=KEEP_LAST_OPTIONS,
            on_toggle_save=self._on_toggle_save,
            on_set_keep_last=self._on_set_keep_last,
            recordings_dir=str(recordings_dir()),
            profiles=self._settings["profiles"],
            active_profiles=self._settings["active_profiles"],
            on_toggle_profile=self._on_toggle_profile,
            on_import_profiles=self._on_import_profiles,
            on_save_profile=self._on_save_profile,
            on_delete_profile=self._on_delete_profile,
            mics=list_input_devices(),
            current_mic=self._settings.get("mic"),
            on_select_mic=self._on_select_mic,
            launch_at_login=self._initial_launch_at_login(),
            on_toggle_login=self._on_toggle_login,
            ui_theme=self._settings.get("ui_theme", "auto"),
            on_set_theme=self._on_set_theme,
            ui_lang=self._settings.get("ui_lang", "uk"),
            on_set_lang=self._on_set_lang,
            dictation_mode=self._settings.get("dictation_mode", "batch"),
            on_set_dictation_mode=self._on_set_dictation_mode,
            hotkey=self._settings["hotkey"],
            lang_hotkeys=self._settings["lang_hotkeys"],
            on_capture_hotkey=self._on_capture_hotkey,
            on_clear_hotkey=self._on_clear_hotkey,
            profile_sets=self._settings["profile_sets"],
            on_save_set=self._on_save_set,
            on_delete_set=self._on_delete_set,
            on_activate_set=self._on_activate_set,
            on_toggle_meeting=self._on_toggle_meeting,
            meeting_capture_mic=self._settings.get("meeting_capture_mic", True),
            meeting_save_file=self._settings.get("meeting_save_file", True),
            meeting_on_top=self._settings.get("meeting_on_top", False),
            meeting_mode=self._settings.get("meeting_mode"),
            meeting_prompt=self._settings.get("meeting_prompt", ""),
            meeting_prompt_source=self._settings.get("meeting_prompt_source", "custom"),
            meeting_source_mode=self._settings.get("meeting_source_mode", "off"),
            meeting_hidden=self._settings.get("meeting_hidden", False),
            meeting_island_opacity=self._settings.get("meeting_island_opacity", 0.92),
            on_set_meeting_mic=self._on_set_meeting_mic,
            on_set_meeting_save=self._on_set_meeting_save,
            on_set_meeting_on_top=self._on_set_meeting_on_top,
            on_set_meeting_lang=self._on_set_meeting_lang,
            on_set_meeting_prompt=self._on_set_meeting_prompt,
            on_set_meeting_prompt_source=self._on_set_meeting_prompt_source,
            on_set_meeting_source_mode=self._on_set_meeting_source_mode,
            on_set_meeting_hidden=self._on_set_meeting_hidden,
            on_set_meeting_opacity=self._on_set_meeting_opacity,
            ft_prompt=self._settings.get("ft_prompt", ""),
            ft_prompt_source=self._settings.get("ft_prompt_source", "auto"),
            on_set_ft_prompt=self._on_set_ft_prompt,
            on_set_ft_prompt_source=self._on_set_ft_prompt_source,
            enhance_enabled=self._settings.get("enhance_enabled", False),
            enhance_model=self._settings.get("enhance_model", ""),
            enhance_style=self._settings.get("enhance_style", "custom"),
            on_set_enhance_enabled=self._on_set_enhance_enabled,
            on_set_enhance_model=self._on_set_enhance_model,
            on_set_enhance_style=self._on_set_enhance_style,
            enhance_status_provider=self._enhance_status,
        )

        # Raise the Input Monitoring / Accessibility system prompts ourselves —
        # macOS never shows them from CGEventTapCreate or a failed paste, so a
        # fresh (or re-signed) .app would otherwise die silently: hotkeys idle,
        # user never asked. Mic and Screen Recording prompt on first use as-is.
        threading.Thread(target=self._request_permissions, daemon=True).start()

        # Hotkey listener is blocking — runs in its own thread. Bindings come from
        # settings and can be re-captured live (set_bindings), no relaunch needed.
        # Profile sets add ⌃⌥<digit> combos alongside the toggle and language keys.
        self._listener = HotkeyListener(
            self._settings["hotkey"],
            self._settings["lang_hotkeys"],
            set_hotkey_bindings(self._settings["profile_sets"]),
        )
        threading.Thread(
            target=self._listener.start,
            args=(self._on_toggle, self._on_mode_select, self._on_activate_set),
            daemon=True,
        ).start()

        # Whisper-server health check at startup.
        threading.Thread(target=self._check_whisper, daemon=True).start()

        # Show the active language in the menu bar from the start.
        self._tray.set_title(self._idle_title())

    def run(self) -> None:
        self._tray.run()

    def _t(self, key: str, **kw) -> str:
        """Localized status-line string in the current app language (see i18n.py)."""
        return t(self._ui_lang, key, **kw)

    def _idle_title(self) -> str:
        """Menu-bar glyph when idle — the active language's flag, or the meeting
        glyph while a capture is running (a dictation finishing mid-meeting must
        not repaint the title back to a flag)."""
        if self._meeting:
            return "🎧"
        return MODE_ICONS.get(self._mode, IDLE_ICON_FALLBACK)

    # ── Hotkey ───────────────────────────────────────────────────────────────
    def _on_toggle(self) -> None:
        if self._busy:
            return
        # Dictation may run alongside a meeting capture as long as the capture
        # isn't taking the mic itself (system-audio-only, e.g. transcribing a
        # video) — the whisper server is serialized in transcriber.py, so the
        # two paths no longer compete. When the capture DOES hold the mic, the
        # dictated speech would land in the transcript too; refuse loudly
        # instead of the old silent ignore.
        if self._meeting and not self._recording and self._meeting_mic:
            self._tray.set_status(self._t("st.micBusyMeeting"))
            self._tray.show_hud(self._t("hud.micBusyMeeting"), "error")
            threading.Timer(1.6, self._tray.hide_hud).start()
            return

        if not self._recording:
            self._recording = True
            # Remember the focused app now, so the result pastes back here even if
            # the user clicks away during a slow transcription.
            self._paste_target = self._paster.capture_target()
            self._streaming = self._settings.get("dictation_mode", "batch") == "streaming"
            # Lazy-start whisper: standby's jetsam reaps the server overnight on
            # 8 GB. Warm it up now, while the user is speaking, so it's ready by
            # the time they stop (no background daemon — it dies with the app).
            self._warm_whisper()
            self._tray.set_title("🔴")
            self._tray.set_status(self._t("st.recording"))
            if self._streaming:
                self._start_streaming()
            else:
                self._recorder.start()
        else:
            self._recording = False
            self._busy = True
            self._tray.set_title("⏳")
            self._tray.set_status(self._t("st.transcribing"))
            target = self._finish_streaming if self._streaming else self._finish
            threading.Thread(target=target, daemon=True).start()

    def _maybe_enhance(self, text: str, lang: str) -> str:
        """Post-dictation LLM styling (postprocessor.py) when enabled. Any
        failure returns the ORIGINAL text — dictation never blocks on the LLM."""
        if not self._settings.get("enhance_enabled"):
            return text
        self._tray.set_title("✨")
        self._tray.set_status(self._t("st.enhancing"))
        # The HUD is the only feedback surface the user actually sees mid-flow —
        # the menu status line is invisible until the menu is opened.
        self._tray.show_hud(self._t("hud.enhancing"), "recognizing")
        preset = self._settings.get("enhance_style", "custom")
        style = compose_style_prompt(
            self._settings["profiles"],
            self._settings["active_profiles"].get(lang, []),
            lang,
            None if preset == "custom" else preset,
        )
        model = self._settings.get("enhance_model", "")
        t0 = time.time()
        result, err = postprocessor.enhance(text, style, model, example=style_example(preset))
        dur = time.time() - t0
        self._tray.hide_hud()
        if err:
            print(f"⚠️ enhance failed ({model}, {dur:.1f}s): {err}")
            self._tray.set_status(f"⚠️ {err[:60]}")
            self._tray.notify(
                "Pysar", self._t("notif.enhanceFailTitle"), self._t("notif.enhanceFailMsg")
            )
            return text
        print(f"✨ enhance ok ({model}, {dur:.1f}s): {len(text)} → {len(result or '')} chars")
        # The HUD is easy to miss; the user must always learn the pasted text
        # is an LLM rewrite, not their verbatim dictation.
        self._tray.notify("Pysar", self._t("notif.enhanceOkTitle"), self._t("notif.enhanceOkMsg"))
        return result or text

    def _finish(self) -> None:
        try:
            wav = self._recorder.stop()
            if wav is None:
                self._tray.set_status(self._t("st.tooShort"))
                self._tray.set_title(self._idle_title())
                return

            # Persist the audio BEFORE transcribing, so a failed/aborted run is
            # recoverable (re-transcribe later instead of re-speaking). Never let
            # a save error break dictation.
            if self._settings["save_recordings"]:
                try:
                    save_recording(wav, self._settings["keep_last"])
                except Exception as e:
                    print(f"⚠️ save_recording failed: {e}")

            # Compose the whisper prompt from this language's active profile group.
            lang = MODES.get(self._mode, MODES[DEFAULT_MODE])["language"]
            active = self._settings["active_profiles"].get(lang, [])
            prompt = compose_prompt(self._settings["profiles"], active, lang)

            t0 = time.time()
            text, err = transcribe(wav, mode=self._mode, prompt=prompt)
            dur = time.time() - t0

            if err:
                self._tray.set_status(f"⚠️ {err[:60]}")
                self._tray.set_title("⚠️")
                return
            if not text:
                self._tray.set_status(self._t("st.silence"))
                self._tray.set_title(self._idle_title())
                return

            text = self._maybe_enhance(text, lang)

            target = getattr(self, "_paste_target", None)
            pasted = self._paster.paste_text(text, target)
            preview = text[:40] + ("…" if len(text) > 40 else "")
            if pasted:
                # Name the app the text landed in — AX can deliver to a window in
                # the background (Spotlight in front), so "✓" alone leaves the user
                # hunting for where it went.
                app_name = target.get("name", "?") if isinstance(target, dict) else "?"
                self._tray.set_status(self._t("st.okIn", app=app_name, dur=dur, preview=preview))
            else:
                # Couldn't deliver to the field — the text is on the clipboard.
                # The menu status line is invisible until the menu is opened, so
                # raise a real notification too.
                self._tray.set_status(self._t("st.inBuffer", preview=preview))
                self._tray.notify(
                    "Pysar", self._t("notif.inBufferTitle"), self._t("notif.inBufferMsg")
                )
            self._tray.set_title(self._idle_title())
        finally:
            self._busy = False

    # ── Streaming dictation ───────────────────────────────────────────────────
    def _start_streaming(self) -> None:
        """Begin a streaming session: spin up a single serialized worker that
        transcribes each segment and types it, in order, then start the recorder
        in segment mode. Segments queue up; the worker drains them one at a time
        so whisper is never hit concurrently (8 GB) and word order is preserved."""
        self._seg_queue = queue.Queue()
        self._first_typed = False
        self._stream_err = None
        self._typed_chars = 0
        # Sentences captured while no field was focused (Spotlight, desktop, an
        # app with no text box). Held here and handed over on the clipboard in one
        # piece at stop — never typed blind into the wrong place.
        self._buffered: list[str] = []
        # Latched once the input field is lost (or was never there): from that
        # point on every sentence goes to the buffer for the rest of the session,
        # even if a field reappears — we can't trust where live typing would land.
        self._buffer_mode = False
        # Latched the first time a segment fails to transcribe (server down), so
        # we fire exactly one notification instead of one per failed segment.
        # Cleared again as soon as a segment succeeds (server recovered).
        self._server_down = False
        # Rolling tail of what we've already transcribed this take, fed to whisper
        # as context on the next segment so it keeps continuity across cuts (far
        # fewer reformulated/invented words on short fragments).
        self._ctx_tail = ""
        self._seg_worker = threading.Thread(target=self._seg_worker_loop, daemon=True)
        self._seg_worker.start()
        self._recorder.start(on_segment=self._enqueue_segment, on_error=self._on_mic_error)
        self._tray.show_hud(self._t("hud.listening"), "listening")

    def _enqueue_segment(self, seg_wav: bytes) -> None:
        """Recorder thread → worker queue. A None sentinel (from stop) ends the worker."""
        if self._seg_queue is not None:
            self._seg_queue.put(seg_wav)

    # Whisper keeps ~224 prompt tokens. Reserve the tail of that for rolling
    # context (~last spoken sentence) so the vocabulary prompt still fits in front.
    _CTX_TAIL_CHARS = 180

    def _seg_worker_loop(self) -> None:
        # Vocabulary prompt is composed once — the active profile group doesn't
        # change mid-dictation. With no profile for this language, fall back to a
        # neutral language seed so the decoder keeps the right script (uk vs ru).
        lang = MODES.get(self._mode, MODES[DEFAULT_MODE])["language"]
        active = self._settings["active_profiles"].get(lang, [])
        base = compose_prompt(self._settings["profiles"], active, lang) or LANG_SEED.get(lang, "")
        self._base_prompt = base
        while True:
            item = self._seg_queue.get()
            if item is None:  # sentinel: stop() has queued the final segment already
                break
            self._process_segment(item, base)

    def _stream_prompt(self, base: str) -> str:
        """Per-segment whisper prompt: the vocabulary/seed base, then the rolling
        tail of what was already said this take. Base goes first and the recent
        context last — whisper keeps the END of an over-long prompt, so the
        nearest context survives truncation."""
        # In auto mode, rolling tail can prime the decoder and cause cross-language
        # translation bleed; skip tail to keep language detection fresh.
        if MODES.get(self._mode, {}).get("language") == "auto":
            return base.strip()
        tail = self._ctx_tail[-self._CTX_TAIL_CHARS :]
        return f"{base} {tail}".strip() if base or tail else ""

    def _process_segment(self, seg_wav: bytes, prompt: str) -> None:
        """Transcribe one segment and deliver it. If a text field is focused, type
        it live; if the field is gone (Spotlight, desktop, no text box) hold it on
        the clipboard instead of typing blind into the wrong place. A failed
        segment is logged and skipped — the session stays alive."""
        self._tray.show_hud(self._t("hud.recognizing"), "recognizing")
        text, err = transcribe(seg_wav, mode=self._mode, prompt=self._stream_prompt(prompt))
        if err:
            # The server is unreachable mid-take. Surface it *now* — not only at
            # Stop — with a red HUD state and a single push, so the user isn't
            # left talking to a dead server thinking it's still listening. The
            # full clip is still saved (if enabled) for re-transcription.
            self._stream_err = err
            self._tray.set_status(f"⚠️ {err[:60]}")
            if not self._server_down:
                self._server_down = True
                self._tray.notify(
                    "Pysar",
                    self._t("notif.serverDownTitle"),
                    self._t("notif.serverDownMsg"),
                )
            self._tray.show_hud(self._t("hud.serverDown"), "error")
            return
        if not text:
            self._tray.show_hud(self._t("hud.listening"), "listening")
            return
        # A segment came back → the server is alive again; re-arm the notice.
        self._server_down = False
        # Extend the rolling context with what was just recognized (typed or
        # buffered alike — continuity is about the words, not where they landed).
        self._ctx_tail = f"{self._ctx_tail} {text}".strip()[-self._CTX_TAIL_CHARS :]

        target = getattr(self, "_paste_target", None)
        if self._buffer_mode or not self._paster.has_editable_focus(target):
            # No field to type into → latch buffer mode for the rest of the
            # session and accumulate this sentence. We don't write the clipboard
            # per sentence (that would clobber it mid-dictation and surprise the
            # user); the whole buffer is placed on the clipboard once, at stop.
            # Don't touch _first_typed: the live-typing space logic stays about
            # what actually went into the field, independent of buffered text.
            if not self._buffer_mode:
                self._buffer_mode = True
                # One-time push so the user knows live typing has stopped and the
                # rest is being collected for a single ⌘V after Stop.
                self._tray.notify(
                    "Pysar",
                    self._t("notif.bufferModeTitle"),
                    self._t("notif.bufferModeMsg"),
                )
            self._buffered.append(text)
            self._tray.set_status(self._t("st.buffering", n=len(self._buffered)))
            self._tray.show_hud(self._t("hud.buffering", n=len(self._buffered)), "buffering")
            return

        # First inserted chunk has no leading space; later ones join with one
        # (whisper supplies in-sentence punctuation).
        chunk = text if not self._first_typed else " " + text
        self._paster.type_text(chunk)
        self._first_typed = True
        self._typed_chars += len(chunk)
        preview = text[:40] + ("…" if len(text) > 40 else "")
        self._tray.set_status(self._t("st.streaming", preview=preview))
        self._tray.show_hud(self._t("hud.listening"), "listening")

    def _on_mic_error(self, msg: str) -> None:
        self._stream_err = msg
        self._tray.set_status(self._t("st.micError"))
        self._tray.notify("Pysar", self._t("notif.micErrorTitle"), self._t("notif.micErrorMsg"))

    def _finish_streaming(self) -> None:
        try:
            wav = self._recorder.stop()  # flushes the final segment through _enqueue_segment

            # Persist the full clip for recoverability, as in batch mode.
            if wav is not None and self._settings["save_recordings"]:
                try:
                    save_recording(wav, self._settings["keep_last"])
                except Exception as e:
                    print(f"⚠️ save_recording failed: {e}")

            # No more segments coming → tell the worker to finish, then wait for it
            # to drain the queue so the last sentence is typed before we go idle.
            if self._seg_queue is not None:
                self._seg_queue.put(None)
            if self._seg_worker is not None:
                self._seg_worker.join(timeout=60)

            self._tray.hide_hud()

            # Sentences captured while no field was focused stay on the clipboard;
            # tell the user with a visible notification so the away-portion isn't
            # forgotten (the menu status line is invisible until the menu opens).
            buffered = getattr(self, "_buffered", [])
            if buffered:
                self._paster.set_clipboard(" ".join(buffered))
                self._tray.notify(
                    "Pysar",
                    self._t("notif.bufferTitle"),
                    self._t("notif.bufferMsg", n=len(buffered)),
                )

            if self._first_typed:
                app_name = (
                    self._paste_target.get("name", "?")
                    if isinstance(getattr(self, "_paste_target", None), dict)
                    else "?"
                )
                self._tray.set_status(self._t("st.streamDone", app=app_name, n=self._typed_chars))
            elif buffered:
                self._tray.set_status(self._t("st.bufferDone", n=len(buffered)))
            elif self._stream_err:
                self._tray.set_status(f"⚠️ {self._stream_err[:60]}")
            else:
                self._tray.set_status(self._t("st.silence"))
            self._tray.set_title(self._idle_title())
        finally:
            self._busy = False

    # ── Transcribe everything (system audio + mic → transcript) ───────────────
    def _on_toggle_meeting(self) -> None:
        """Menu toggle. Start/stop a system-audio + mic capture into the live
        transcript window. Independent of the dictation hotkey."""
        if self._meeting:
            threading.Thread(target=self._stop_meeting, daemon=True).start()
        else:
            self._start_meeting()

    def _start_meeting(self) -> None:
        # Starting a capture mid-dictation stays blocked (finish the sentence
        # first); the reverse — dictating while a capture runs — is allowed when
        # the capture doesn't hold the mic (see _on_toggle). Whisper requests
        # from the two paths are serialized in transcriber.py.
        if self._recording or self._busy:
            self._tray.set_status(self._t("st.transcribing"))
            return
        if self._meeting_stopping:
            # The previous capture is still draining its queue (can take up to
            # a minute); starting a second one on top would race the worker and
            # desync the menu state (stress test 08.07.2026, bug 1).
            self._tray.set_status(self._t("st.meetingStopping"))
            return
        self._meeting = True
        self._meeting_tails = {}
        self._meeting_server_down = False
        self._warm_whisper()

        spk_labels = {"sys": self._t("transcript.spk.sys"), "mic": self._t("transcript.spk.mic")}
        if self._transcript_window is None:
            self._transcript_window = TranscriptWindow(
                self._t("transcript.title"), on_frame_change=self._on_island_frame_change
            )
        self._transcript_window.set_source_labels(spk_labels)
        self._transcript_window.clear()
        self._transcript_window.apply_theme(self._settings.get("ui_theme", "auto"))
        self._transcript_window.set_on_top(self._settings.get("meeting_on_top", False))
        self._transcript_window.set_frame(self._settings.get("meeting_island_frame"))
        self._transcript_window.set_opacity(self._settings.get("meeting_island_opacity", 0.92))
        # "Record without the window" — keep the .md autosave, skip the island.
        hidden = self._settings.get("meeting_hidden", False)
        if not hidden:
            self._transcript_window.show(self._t("transcript.title"))
        elif not self._settings.get("meeting_save_file", True):
            # Hidden AND not saving → nothing would be captured anywhere; warn.
            self._tray.notify(
                "Pysar", self._t("notif.hiddenNoSaveTitle"), self._t("notif.hiddenNoSaveMsg")
            )
        if self._settings.get("meeting_save_file", True):
            self._transcript_file = TranscriptFile()
            self._transcript_file.set_source_labels(spk_labels)
            try:
                self._transcript_file.open()
            except Exception as e:
                print(f"⚠️ transcript file open failed: {e}")
                self._transcript_file = None
        else:
            self._transcript_file = None

        # Single serialized worker drains segments one at a time (whisper is never
        # hit concurrently), mirroring the streaming dictation path.
        self._meeting_queue = queue.Queue()
        self._meeting_worker = threading.Thread(target=self._meeting_worker_loop, daemon=True)
        self._meeting_worker.start()

        capture_mic = self._settings.get("meeting_capture_mic", True)
        self._meeting_mic = bool(capture_mic)
        source_mode = self._settings.get("meeting_source_mode", "off")
        if self._sysrec is None:
            self._sysrec = SystemAudioRecorder(capture_mic=capture_mic, source_mode=source_mode)
        else:
            self._sysrec.set_capture_mic(capture_mic)
            self._sysrec.set_source_mode(source_mode)
        self._sysrec.start(on_segment=self._enqueue_meeting, on_error=self._on_meeting_error)

        self._tray.set_meeting_active(True)
        self._tray.set_title("🎧")
        self._tray.set_status(self._t("st.meetingOn"))

    def _enqueue_meeting(self, seg_wav: bytes, source: str | None = None) -> None:
        if self._meeting_queue is not None:
            self._meeting_queue.put((seg_wav, source))

    def _on_meeting_error(self, msg: str) -> None:
        print(f"⚠️ system capture: {msg}")
        self._tray.notify(
            "Pysar", self._t("notif.captureErrorTitle"), self._t("notif.captureErrorMsg")
        )
        threading.Thread(target=self._stop_meeting, daemon=True).start()

    def _meeting_worker_loop(self) -> None:
        # Meeting language is its own setting; None / unknown falls back to the live
        # dictation mode. Resolved once per capture so it's stable for the session.
        mode = self._settings.get("meeting_mode") or self._mode
        if mode not in MODES:
            mode = self._mode
        lang = MODES.get(mode, MODES[DEFAULT_MODE])["language"]
        # Source switch: "custom" uses the context-hint field (falling back to
        # profiles only when it's empty); "profiles" always uses the active speech
        # profiles for the meeting language.
        source = self._settings.get("meeting_prompt_source", "custom")
        custom = (self._settings.get("meeting_prompt") or "").strip()
        if source == "custom" and custom:
            base = custom
        else:
            active = self._settings["active_profiles"].get(lang, [])
            base = compose_prompt(self._settings["profiles"], active, lang) or LANG_SEED.get(
                lang, ""
            )
        while True:
            item = self._meeting_queue.get()
            if item is None:  # sentinel queued by _stop_meeting
                break
            seg_wav, source = item
            self._process_meeting_segment(seg_wav, source, base, mode)

    def _process_meeting_segment(
        self, seg_wav: bytes, source: str | None, base: str, mode: str
    ) -> None:
        # When the mic is off the whole stream is system audio, so label it
        # "System" even in the mixed ("off") mode where syscap can't tag a source.
        if source is None and not self._settings.get("meeting_capture_mic", True):
            source = "sys"
        # In auto mode, rolling tail primes decoder and causes cross-language
        # bleed; use only base to keep language detection fresh. Otherwise feed
        # this source's own rolling tail (keeping each speaker's context separate).
        if MODES.get(mode, {}).get("language") == "auto":
            prompt = base.strip()
        else:
            tail = self._meeting_tails.get(source, "")[-self._CTX_TAIL_CHARS :]
            prompt = f"{base} {tail}".strip() if (base or tail) else ""
        text, err = transcribe(seg_wav, mode=mode, prompt=prompt)
        if err:
            if not self._meeting_server_down:
                self._meeting_server_down = True
                self._tray.notify(
                    "Pysar", self._t("notif.serverDownTitle"), self._t("notif.serverDownMsg")
                )
            self._tray.set_status(f"⚠️ {err[:60]}")
            return
        if not text:
            return
        self._meeting_server_down = False
        ts = datetime.now()  # stamp at transcription time = when the line was said
        prev = self._meeting_tails.get(source, "")
        self._meeting_tails[source] = f"{prev} {text}".strip()[-self._CTX_TAIL_CHARS :]
        if self._transcript_window is not None:
            self._transcript_window.append(text, source, ts)
        if self._transcript_file is not None:
            with contextlib.suppress(Exception):
                self._transcript_file.append(text, source, ts)
        preview = text[:40] + ("…" if len(text) > 40 else "")
        self._tray.set_status(self._t("st.meetingLine", preview=preview))

    def _stop_meeting(self) -> None:
        if not self._meeting:
            return
        self._meeting = False
        self._meeting_mic = False
        self._meeting_stopping = True  # _start_meeting refuses until the drain ends
        saved_path = None
        try:
            # Stop capture first (flushes the trailing segment into the queue), then
            # drain the worker so the final sentence lands before the file is closed.
            if self._sysrec is not None:
                with contextlib.suppress(Exception):
                    self._sysrec.stop()
            if self._meeting_queue is not None:
                self._meeting_queue.put(None)
            if self._meeting_worker is not None:
                self._meeting_worker.join(timeout=60)

            if self._transcript_file is not None:
                saved_path = str(self._transcript_file.path or "")
                self._transcript_file.close()
                self._transcript_file = None
        finally:
            # The UI reset must land even if the drain/close above blew up —
            # otherwise the menu keeps offering "stop transcription" for a
            # capture that no longer exists (stress test 08.07.2026, bug 1).
            self._meeting_stopping = False
            if self._transcript_window is not None:
                self._transcript_window.hide()  # island shows only while transcribing
            self._tray.set_meeting_active(False)
            self._tray.set_status(self._t("st.meetingOff"))
            self._tray.set_title(self._idle_title())
            if saved_path:
                self._tray.notify(
                    "Pysar",
                    self._t("notif.meetingSavedTitle"),
                    self._t("notif.meetingSavedMsg", path=saved_path),
                )

    # ── Mode selection ───────────────────────────────────────────────────────
    def _on_mode_select(self, code: str) -> None:
        self._mode = code
        # Persist so the chosen language survives a restart.
        self._settings["mode"] = code
        save_settings(self._settings)
        self._tray.set_current_mode(code)  # update the menu checkmark
        self._tray.set_status(self._t("st.mode", label=MODE_LABELS[code]))
        # Reflect the language in the menu-bar icon for instant confirmation,
        # unless a record/transcribe cycle or an active meeting owns the title.
        if not self._recording and not self._busy and not self._meeting:
            self._tray.set_title(self._idle_title())

    # ── Recording-archive settings ───────────────────────────────────────────
    def _on_toggle_save(self, enabled: bool) -> None:
        self._settings["save_recordings"] = enabled
        save_settings(self._settings)
        self._tray.set_status(self._t("st.saving") if enabled else self._t("st.memoryOnly"))

    def _on_set_keep_last(self, n: int) -> None:
        self._settings["keep_last"] = n
        save_settings(self._settings)
        self._tray.set_status(self._t("st.keepLast", n=n))

    # ── Speech profiles ───────────────────────────────────────────────────────
    # Identity is (name, language) throughout: the same name may exist once per
    # language (a uk "Я" and a ru "Я" are two different profiles), so every
    # caller here is expected to know and pass the language rather than have it
    # re-derived by an ambiguous name-only lookup.
    def _on_toggle_profile(self, name: str, language: str, active: bool) -> None:
        # A profile belongs to its own language group, independent of the current
        # mode — toggling "English" affects the en group, "Розробка" the uk group.
        group = self._settings["active_profiles"].setdefault(language, [])
        if active and name not in group:
            group.append(name)
        elif not active and name in group:
            group.remove(name)
        save_settings(self._settings)
        self._tray.set_status(self._t("st.profileOn" if active else "st.profileOff", name=name))

    def _on_save_profile(
        self,
        name: str,
        language: str,
        prompt: str,
        original_name: str | None,
        original_language: str | None = None,
    ) -> tuple[list[dict] | None, str | None]:
        """Add or edit a profile from the Settings-window editor. Returns
        (updated_profiles | None, error). Keeps the active group in sync when a
        profile is renamed or re-languaged, so a toggled-on group doesn't lose
        its member or bleed into the wrong language's group."""
        updated, err = upsert_profile(
            self._settings["profiles"],
            name,
            language,
            prompt,
            original_name,
            original_language=original_language,
        )
        if err:
            return None, err
        if original_name and (original_name != name or original_language != language):
            old_group = self._settings["active_profiles"].get(original_language or language, [])
            if original_name in old_group:
                old_group.remove(original_name)
                new_group = self._settings["active_profiles"].setdefault(language, [])
                if name not in new_group:
                    new_group.append(name)
        self._settings["profiles"] = updated
        save_settings(self._settings)
        return updated, None

    def _on_delete_profile(self, name: str, language: str) -> list[dict]:
        """Remove a profile and drop it from its active group."""
        self._settings["profiles"] = remove_profile(self._settings["profiles"], name, language)
        group = self._settings["active_profiles"].get(language, [])
        if name in group:
            group.remove(name)
        save_settings(self._settings)
        return self._settings["profiles"]

    def _on_import_profiles(
        self, text: str, force: bool = False
    ) -> tuple[list[dict] | None, int, str | None, list[str]]:
        """Parse pasted JSON, merge into the library, persist. Returns
        (updated_profiles | None, added_count, error, conflicts) for the tray to
        react. If the import would silently overwrite an existing profile under
        a different name/prompt and `force` isn't set, nothing is saved — the
        conflicting names come back so the UI can ask before it happens."""
        incoming, err = parse_imported(text)
        if err:
            return None, 0, err, []
        conflicts = import_conflicts(self._settings["profiles"], incoming)
        if conflicts and not force:
            return None, 0, None, conflicts
        self._settings["profiles"] = merge_profiles(self._settings["profiles"], incoming)
        save_settings(self._settings)
        return self._settings["profiles"], len(incoming), None, []

    def _on_select_mic(self, name: str | None) -> None:
        self._settings["mic"] = name
        save_settings(self._settings)
        self._recorder.set_device(name)
        self._tray.set_status(self._t("st.mic", name=name) if name else self._t("st.defaultMic"))

    def _initial_launch_at_login(self) -> bool:
        """The checkmark's starting state. Prefer the OS's real SMAppService
        status so the toggle reflects reality after a reboot (and after the user
        changes it in System Settings); fall back to our saved setting only when
        the status can't be read. Reconcile the saved value to what we found."""
        real = login_item_enabled()
        if real is None:
            return bool(self._settings.get("launch_at_login", False))
        if self._settings.get("launch_at_login") != real:
            self._settings["launch_at_login"] = real
            save_settings(self._settings)
        return real

    def _on_toggle_login(self, enabled: bool) -> None:
        self._settings["launch_at_login"] = enabled
        save_settings(self._settings)
        self._tray.set_status(self._t("st.loginOn") if enabled else self._t("st.loginOff"))

    def _on_set_theme(self, theme: str) -> None:
        self._settings["ui_theme"] = theme
        save_settings(self._settings)
        if self._transcript_window is not None:
            self._transcript_window.apply_theme(theme)

    def _on_set_lang(self, lang: str) -> None:
        self._settings["ui_lang"] = lang
        self._ui_lang = lang  # keep status-line strings in the new language
        save_settings(self._settings)

    def _on_set_dictation_mode(self, mode: str) -> None:
        self._settings["dictation_mode"] = mode if mode in ("batch", "streaming") else "batch"
        save_settings(self._settings)
        self._tray.set_status(
            self._t("st.dictStreaming" if mode == "streaming" else "st.dictBatch")
        )

    # ── Transcribe-everything (meeting) settings ──────────────────────────────
    # ── Enhance (post-dictation LLM styling) settings ─────────────────────────
    def _on_set_enhance_enabled(self, on: bool) -> None:
        self._settings["enhance_enabled"] = bool(on)
        save_settings(self._settings)
        if on:
            # Warm the model in the background so the first dictation isn't cold.
            threading.Thread(
                target=postprocessor.preload,
                args=(self._settings.get("enhance_model", ""),),
                daemon=True,
            ).start()

    def _on_set_enhance_model(self, model: str) -> None:
        self._settings["enhance_model"] = (model or "").strip()
        save_settings(self._settings)
        if self._settings.get("enhance_enabled"):
            threading.Thread(
                target=postprocessor.preload,
                args=(self._settings["enhance_model"],),
                daemon=True,
            ).start()

    def _on_set_enhance_style(self, style: str) -> None:
        allowed = {"custom"} | {p["key"] for p in STYLE_PRESETS}
        self._settings["enhance_style"] = style if style in allowed else "custom"
        save_settings(self._settings)

    def _enhance_status(self) -> dict:
        """Lazy Ollama probe for the settings screen (called on open, no polling)."""
        return {"alive": postprocessor.is_ollama_alive(), "models": postprocessor.list_models()}

    def _on_set_meeting_mic(self, on: bool) -> None:
        self._settings["meeting_capture_mic"] = bool(on)
        save_settings(self._settings)

    def _on_set_meeting_save(self, on: bool) -> None:
        self._settings["meeting_save_file"] = bool(on)
        save_settings(self._settings)

    def _on_set_meeting_on_top(self, on: bool) -> None:
        self._settings["meeting_on_top"] = bool(on)
        save_settings(self._settings)

    def _on_set_meeting_lang(self, mode: str | None) -> None:
        # None / unknown → inherit the live dictation mode at capture time.
        self._settings["meeting_mode"] = mode if mode in MODES else None
        save_settings(self._settings)

    def _on_set_meeting_prompt(self, text: str) -> None:
        self._settings["meeting_prompt"] = (text or "").strip()
        save_settings(self._settings)

    def _on_set_meeting_source_mode(self, mode: str) -> None:
        self._settings["meeting_source_mode"] = mode if mode in ("off", "fast", "smart") else "off"
        save_settings(self._settings)

    def _on_set_meeting_prompt_source(self, source: str) -> None:
        self._settings["meeting_prompt_source"] = (
            source if source in ("custom", "profiles") else "custom"
        )
        save_settings(self._settings)

    def _on_set_ft_prompt(self, text: str) -> None:
        self._settings["ft_prompt"] = (text or "").strip()
        save_settings(self._settings)

    def _on_set_ft_prompt_source(self, source: str) -> None:
        self._settings["ft_prompt_source"] = source if source in ("auto", "custom") else "auto"
        save_settings(self._settings)

    def _on_set_meeting_hidden(self, on: bool) -> None:
        self._settings["meeting_hidden"] = bool(on)
        save_settings(self._settings)

    def _on_set_meeting_opacity(self, value: float) -> None:
        with contextlib.suppress(Exception):
            v = max(0.0, min(1.0, float(value)))
            self._settings["meeting_island_opacity"] = v
            save_settings(self._settings)
            if self._transcript_window is not None:
                self._transcript_window.set_opacity(v)

    def _on_island_frame_change(self, frame: dict) -> None:
        # Persist the floating island's position/size so it reopens where the
        # user left it. Called on every move/resize from the window delegate.
        with contextlib.suppress(Exception):
            self._settings["meeting_island_frame"] = {
                "x": float(frame["x"]),
                "y": float(frame["y"]),
                "w": float(frame["w"]),
                "h": float(frame["h"]),
            }
            save_settings(self._settings)

    def _on_capture_hotkey(self, slot: str) -> None:
        """Capture the next keypress and rebind `slot` to it, live (no relaunch).
        `slot` is "__toggle__" (dictation), a language code (switch), or
        "set:<index>" (a profile set's override binding)."""

        def apply(binding: dict) -> None:
            kc, mods = binding["keycode"], binding["mods"]
            if not is_bindable(kc, mods):
                # A bare printable key would also type — make the user add a modifier.
                self._tray.set_status(self._t("st.needMod"))
                return
            if slot == "__toggle__":
                self._settings["hotkey"] = {"keycode": kc, "mods": mods}
            elif slot.startswith("set:"):
                idx = int(slot[4:])
                sets = self._settings["profile_sets"]
                if 0 <= idx < len(sets):
                    sets[idx]["keycode"], sets[idx]["mods"] = kc, mods
            else:
                for h in self._settings["lang_hotkeys"]:
                    if h["action"] == slot:
                        h["keycode"], h["mods"] = kc, mods
                        break
            save_settings(self._settings)
            self._apply_bindings()
            self._tray.update_hotkeys(self._settings["hotkey"], self._settings["lang_hotkeys"])
            self._tray.set_status(self._t("st.hotkeySet", label=binding_label(kc, mods)))

        self._listener.begin_capture(apply)

    def _on_clear_hotkey(self, action: str) -> None:
        """Reset a slot's shortcut, live. A language slot goes back to unassigned;
        a "set:<index>" slot reverts to its default ⌃⌥<digit> (keycode None)."""
        if action.startswith("set:"):
            idx = int(action[4:])
            sets = self._settings["profile_sets"]
            label = sets[idx]["name"] if 0 <= idx < len(sets) else action
            if 0 <= idx < len(sets):
                sets[idx]["keycode"], sets[idx]["mods"] = None, []
        else:
            label = MODE_LABELS.get(action, action)
            for h in self._settings["lang_hotkeys"]:
                if h["action"] == action:
                    h["keycode"], h["mods"] = None, []
                    break
        save_settings(self._settings)
        self._apply_bindings()
        self._tray.update_hotkeys(self._settings["hotkey"], self._settings["lang_hotkeys"])
        self._tray.set_status(self._t("st.cleared", label=label))

    # ── Profile sets ──────────────────────────────────────────────────────────
    def _apply_bindings(self) -> None:
        """Rebuild the listener's binding table from the live settings — toggle,
        language switches and the ⌃⌥<digit> profile-set combos together."""
        self._listener.set_bindings(
            self._settings["hotkey"],
            self._settings["lang_hotkeys"],
            set_hotkey_bindings(self._settings["profile_sets"]),
        )

    def _on_save_set(self, index, name: str, members: list):
        """Create (index None) or replace (index given) a profile set. Returns
        (sets, error); the set hotkeys are re-bound so ⌃⌥<digit> works at once.

        Each member is `{"name": ..., "language": ...}` — kept as a pair (not
        just a name) so a set can't get confused between two profiles that
        share a name in different languages."""
        name = (name or "").strip()
        if not name:
            return self._settings["profile_sets"], "A set needs a name."
        members = [
            m
            for m in (members or [])
            if isinstance(m, dict) and m.get("name") and m.get("language")
        ]
        sets = self._settings["profile_sets"]
        if index is None:
            if len(sets) >= MAX_PROFILE_SETS:
                return sets, f"At most {MAX_PROFILE_SETS} sets."
            sets.append({"name": name, "members": members})
        elif 0 <= index < len(sets):
            sets[index] = {"name": name, "members": members}
        else:
            return sets, "Set not found."
        save_settings(self._settings)
        self._apply_bindings()
        return sets, None

    def _on_delete_set(self, index: int) -> list[dict]:
        sets = self._settings["profile_sets"]
        if 0 <= index < len(sets):
            sets.pop(index)
            save_settings(self._settings)
            self._apply_bindings()
        return sets

    def _on_activate_set(self, index: int) -> None:
        """Make a set's members the entire active selection (⌃⌥<digit> or the
        Settings button). Replaces every language group at once."""
        sets = self._settings["profile_sets"]
        if not (0 <= index < len(sets)):
            return
        s = sets[index]
        self._settings["active_profiles"] = regroup_active(self._settings["profiles"], s["members"])
        save_settings(self._settings)
        self._tray.set_active_profiles(self._settings["active_profiles"])
        self._tray.set_status(self._t("st.setOn", name=s["name"]))

    # ── Permissions ──────────────────────────────────────────────────────────
    def _request_permissions(self) -> None:
        """Fire the Input Monitoring / Accessibility system dialogs (macOS).

        Off the main thread — both calls can block on TCC IPC. Denials are not
        fatal here: the hotkey listener keeps polling for Input Monitoring, and
        the paste path falls back to the clipboard without Accessibility.
        """
        if sys.platform != "darwin":
            return
        from .backend import _permissions

        if _permissions.input_monitoring_status() != _permissions.GRANTED:
            print("🔐 requesting Input Monitoring permission…")
            _permissions.request_input_monitoring()
        if not _permissions.ensure_accessibility(prompt=True):
            print("⚠️ Accessibility not granted — paste falls back to the clipboard")

    # ── Whisper health ───────────────────────────────────────────────────────
    def _check_whisper(self) -> None:
        time.sleep(0.5)
        if not is_alive():
            self._tray.set_status(self._t("st.whisperDown"))
            self._tray.set_title("⚠️")

    def _warm_whisper(self) -> None:
        """If the server is down (jetsam reaped it during standby), bring it back
        on a background thread so model load overlaps with the user speaking. The
        normal case — server already up — returns instantly without a thread."""
        if server.is_up():
            return

        def _go() -> None:
            self._tray.set_status(self._t("st.whisperStarting"))
            server.ensure_running()

        threading.Thread(target=_go, daemon=True).start()


def main() -> None:
    """Console-script entry point (see pyproject.toml [project.scripts])."""
    from .logsetup import setup_logging

    log = setup_logging()  # tee stdout/stderr to a file + catch crashes
    if log:
        print(f"📝 logging to {log}")
    VoiceTyper().run()


if __name__ == "__main__":
    main()
