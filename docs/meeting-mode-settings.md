# Meeting-mode settings — process log

Staged build of the **"Transcribe everything"** (system audio + mic → live
transcript) configuration UI, so a breakage is isolated to one phase. Each
phase: what changed, what was verified, where it can still break.

**Why this exists:** the meeting/transcriber feature already captures and
transcribes, but nothing about it was configurable — language and prompt were
silently inherited from the *dictation* mode, the transcript was always written
to disk, and the window had no controls. This adds a dedicated Settings screen.

**Scope decision (after competitor scan — MacWhisper / superwhisper):** the
niche leaders ship almost *no* per-meeting config — they win on frictionless
capture + privacy, not knobs. So the MVP stays lean: 3 clear toggles + our one
differentiator (custom prompt/vocab, which MacWhisper does *not* expose for
meetings) + a quiet language inherit. Deliberately **deferred** (leaders don't
ship these either): per-window/app source pick, mic-device chooser. **Roadmap,
not v1:** speaker labels (diarization), auto meeting-detection.

**MVP control set:**
1. Audio source — system + mic / system only  (`meeting_capture_mic`, already read)
2. Save transcript to file + "Open folder"     (`meeting_save_file`)
3. Transcript window always-on-top             (`meeting_on_top`)
4. Transcription language (quiet; inherits dictation when unset)  (`meeting_mode`)
5. Context hint / initial prompt               (`meeting_prompt`)

**Design line:** taste-skill, within the existing settings-window style (same
`.row.nav` → drill-in `.screen` pattern as Profiles / Hotkeys). The tray item
stays the start/stop trigger; this screen is configuration only.

**Lives on the uncommitted transcriber branch** (syscap.py / transcript_window.py
/ transcripts.py + app.py meeting wiring) — still quarantined until this is done
and tested, then committed as its own unit, separate from the dictation core.

---

## Phase 0 — settings schema + this doc ✅

**Done:** added `meeting_capture_mic` / `meeting_save_file` / `meeting_on_top` /
`meeting_mode` (None = inherit) / `meeting_prompt` to `DEFAULTS` in
`src/recordings.py`, with a comment block. No behaviour change.
**Verified:** `DEFAULTS` exposes all five `meeting_*` keys; ruff clean;
`pytest` → 128 passed.
**Can break:** nothing — additive defaults, merged onto stored settings on load.

## Phase 1 — i18n strings ✅

**Done:** added the meeting-screen string block (nav row + screen intro + each
control's label/help + language-inherit option + prompt placeholder) to BOTH the
`uk` and `en` tables in `src/i18n.py` — 17 new keys per language.
**Verified:** `uk`/`en` key sets identical (parity holds), no empty values, ruff
clean, `pytest` → 128 passed.
**Can break:** nothing — pure strings; a missing `data-i18n` target just shows the
HTML fallback text.

## Phase 2 — back-end plumbing (Tray ctor / state / handlers / app callbacks) ✅

**Done:** end-to-end wiring mirroring the dictation-mode/theme pattern, no UI yet:
- `app.py`: 5 callbacks (`_on_set_meeting_mic/save/on_top/lang/prompt`), each
  persisting to `self._settings` + `save_settings`; passed into the `Tray(...)`
  call alongside the current values.
- `_macos.py` `Tray.__init__`: 5 value params + 5 `on_set_*` params, stored as
  `self._meeting_*`; also stored `self._modes` (reused for the language picker).
- `Tray._settings_state`: emits `meeting_capture_mic/save_file/on_top/mode/prompt`,
  `meeting_modes` (the `(code,label)` language options), and `transcripts_dir`.
- `Tray._open_settings` handlers: `set_meeting_mic/save/on_top/lang/prompt` +
  `open_transcripts_folder`; setter methods `_set_meeting_*` (mirror + callback)
  and `_open_transcripts_folder`; `_transcripts_dir()` lazy-imports `transcripts`.
**Verified:** ruff clean; `_macos.py`/`app.py` parse; `pytest` → 128 passed. Live
state-dict round-trip is verified in Phase 3 (needs the window open).
**Can break:** `meeting_mode` is validated against `MODES`/`self._modes`; an
unknown value coerces to `None` (= inherit), so a stale stored code can't crash.

## Phase 3 — Settings UI (drill-in screen HTML/JS) ✅

**Done (`settings_window.py`):** a "Meetings & calls" section on the main screen
with a `go-meeting` nav row; a new `#screen-meeting` drill-in (modeled on the
Hotkeys screen) holding: capture-mic toggle (`mt-mic`), on-top toggle (`mt-ontop`),
language `<select>` (`mt-lang`, inherit option + `meeting_modes`), save toggle
(`mt-save`), folder path + Open button (`mt-path`/`mt-open` → `open_transcripts_folder`),
and a context-hint `<textarea>` (`mt-prompt`). Wired `show("meeting")`, the
`go-meeting`/`back-mt` nav, all change→`send(...)` handlers in the static-controls
IIFE, and the inherit-option relabel in `applyI18n`.
**Verified:** ruff clean; `build_html(state)` renders and contains
`screen-meeting`/`go-meeting`/`mt-*`/`set_meeting_lang`/`open_transcripts_folder`;
i18n parity holds; `pytest` → 128 passed. Live click-through verified at the end.
**Can break:** the `mt-lang` value is sent as `null` for the empty (inherit)
option; Python coerces unknown → `None`. Toggles default ON via `!== false` so a
missing state key reads as enabled (matches `DEFAULTS`).

## Phase 4 — capture behaviour (language / prompt / save-file gate) ✅

**Done (`app.py`):** `_start_meeting` now opens a `TranscriptFile` only when
`meeting_save_file` is on (else `None` → nothing written, append is already
guarded). `_meeting_worker_loop` resolves the meeting language from `meeting_mode`
(falling back to the live dictation mode when unset/unknown) and uses a custom
`meeting_prompt` as the prompt base when present, otherwise the previous
profile-composed base. `_process_meeting_segment` takes the resolved `mode` and
passes it to `transcribe(...)` instead of the dictation `self._mode`.
**Verified:** ruff clean, `app.py` parses, `pytest` → 128 passed.
**Can break:** mode validated against `MODES` (unknown → dictation mode). With
save off, `_stop_meeting`'s "saved" notification path sees no file → no false
"saved" message (it checks `self._transcript_file`).

## Phase 5 — transcript window on-top + open transcripts folder ✅

**Done:** `transcript_window.py` gained `set_on_top(bool)` + `_apply_level()`
(`NSFloatingWindowLevel` / `NSNormalWindowLevel`), applied on the main thread and
re-applied inside `show()`’s `_go`. `_start_meeting` calls `set_on_top(meeting_on_top)`
before showing. Open-folder is the Phase-2 `open_transcripts_folder` handler
(`subprocess open` the transcripts dir).
**Verified:** ruff clean, both files parse, `pytest` → 128 passed.
**Can break:** `setLevel_` is wrapped in `suppress(Exception)`; if the AppKit
constant import ever fails the window simply stays at normal level — no crash.

---

## Live verification (after all phases)

- Launched the **existing** `/Applications/Pysar.app` (no `make app` rebuild — a
  rebuild would re-invalidate the freshly re-granted TCC permissions). The bundle
  runs the live `src/`, so it loads these changes directly. Started clean: new
  "Pysar start" banner, hotkey listener up, **no import/wiring errors** — a bad
  handler reference or syntax slip would have crashed on launch.
- `build_html(state)` render test passed (screen + every control present).
- **Left for the user (acceptance):** open Settings → *Meetings & calls* → drill
  in; flip the toggles / pick a language / type a hint and confirm they persist
  (re-open the screen, check `settings.json`).
- **Still NOT addressed (separate, pre-existing):** the capture **teardown /
  mic-release on picker-hang** — the bug that forced the reboot. This phase was
  the *settings UI only*. Configuring is safe; *starting an actual capture* still
  carries the earlier hang risk until teardown is hardened (next task).
- **Screenshot acceptance (2026-06-25):** the *Meetings & calls* screen renders
  correctly with all controls (toggles, language select, save+open-folder, hint).

---

## Backlog & open questions (from user review 2026-06-25) — NOT built yet

### Bug — UI
- **Context-hint textarea overflows the panel.** Dragging its resize handle to the
  right pushes it past the window/interface edge. Fix: constrain to vertical-only
  resize (`resize: vertical`) + `max-width:100%` (and probably a sensible
  `max-height`). Small CSS fix in `settings_window.py`. *(Quick win.)*

### Design — needs a decision (context hint vs dictation profiles)
- The meeting **context hint** currently has its own free-text field, but the app
  already has dictation **initial-prompt profiles**. Decide the model:
  - (a) reuse the existing dictation profiles here directly; or
  - (b) a **toggle "use dictation profiles"** — when ON, pull the profile-composed
    prompt and **disable/grey this field**; when OFF, type a meeting-specific hint.
- Either way, add a **word/token limit + live auto-count in the label** (mirror the
  profiles token meter), and clarify in the help text that it's the same mechanism
  as profiles. **Open — think it through before building.**

### Feature backlog — meeting mode
- **Mic input-device chooser** on this screen (the deferred per-device pick — user
  now wants it surfaced here).
- **Active-capture controls:** a **Pause** (pause/resume the live transcription)
  and a **Stop** that finalizes the current file so the next start begins a **new
  file**. (Today it's a single start/stop tray toggle.)
- **Quiet dictation / no visible window:** a setting to run capture **without**
  showing the floating transcript window (headless transcription to file only).
- **On-top window follows the active display:** when the user switches screens,
  the floating transcript should pull to the active display; make this a separate
  on/off toggle. (Extends the Phase-5 always-on-top.)

