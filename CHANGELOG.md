# Changelog

All notable changes to this project are tracked in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Meeting transcript filter** (`src/meetingfilter.py`) — post-ASR cleanup for the "transcribe everything" pipeline, driven by whisper-server `verbose_json` metadata (new `transcribe_meeting()` in `src/transcriber.py`; dictation still uses the plain path). Three failure modes from a live call (16.07.2026) are filtered per meeting: **cross-channel echo** in "smart" source mode (speakers instead of headphones put the remote voice into both the system channel and the mic, so every phrase appeared twice under alternating You/System labels — dropped via word-run coverage ≥ 0.55 against the other channel's last 25 s, runs of ≥2 shared words only, so same-topic replies never match); **backchannel hallucinations** (hums/"дякую" decoding as "Obrigada"-style foreign filler — dropped on duration-weighted `no_speech_prob` > 0.55 or short blurbs with `avg_logprob` < −1.1); **language-detection flicker** in auto mode (short foreign-detected segments against the session's vote-dominant language are dropped; long passages and high-confidence detections pass, so genuine language switches survive). Dropped lines are logged with a reason and kept out of the rolling prompt tail, so a mic-bleed echo can't prime the decoder with the other speaker's words.
- **Rolling context in streaming recognition.** Each segment is now transcribed with the tail (~180 chars) of what was already said this take fed back as the whisper prompt, so recognition keeps continuity across cuts — far fewer reformulated/invented words on short fragments. A neutral per-language seed prompt (`LANG_SEED`, currently `uk`/`ru`) primes the decoder even when no speech profile is active, stopping `large-v3-turbo`'s drift into Russian script on Ukrainian audio. Segmentation now waits for a fuller clause before cutting (`PAUSE_SEC` 0.6→0.9, `MIN_SEG_SEC` 0.8→1.6) instead of slicing sub-sentence fragments, and per-segment normalization caps gain at +12 dB (was +18) so quiet/short scraps aren't amplified into hallucinations.
- **One-command install** — `install.sh` (a `curl … | bash` one-liner) bootstraps a clean Mac end-to-end: checks for Apple Silicon, installs missing prerequisites via Homebrew (`cmake`, `git`, Python), clones the repo if needed, then runs `make setup` + `make app`. Idempotent (re-run to update). Also added a `make all` target (`setup` + `app`). README install collapsed to the single command.
- **Streaming dictation mode** (opt-in via Settings → Dictation). Types each sentence into the focused field as you speak instead of one transcription at the end. New `src/segmenter.py` cuts audio on natural pauses (adaptive RMS noise floor, never mid-word; hard `MAX_SEG_SEC` cap for pause-free run-ons). A serialized queue worker transcribes and types segments one at a time so word order holds and whisper is never run concurrently on 8 GB. Sentences are inserted as synthetic Unicode key events (`Paster.type_text`) — no clipboard involvement, so the user's clipboard is never clobbered mid-dictation. Batch remains the default. Tunable params in `src/config.py` (`PAUSE_SEC`, `MIN_SEG_SEC`, `MAX_SEG_SEC`, `SILENCE_MARGIN`).
- **Focus-aware buffering for streaming.** Before typing each sentence, `Paster.has_editable_focus()` checks the keyboard focus is a real text field (Spotlight/Siri overlay or a non-editable target ⇒ no field; same-app-as-start ⇒ trusted, to protect the Electron/Claude path). On loss it latches **buffer mode**: the rest of the take is collected and placed on the clipboard in one piece at Stop with a single notification — nothing is typed blind into the wrong place.
- **Floating status HUD** (`src/backend/_hud.py`) that drops out of the app's own menu-bar icon, showing listening / recognizing / buffering. A non-activating, click-through `NSPanel` that never steals focus; failures are swallowed so a missing overlay never breaks dictation. It anchors under the status-bar button, springs in on first show (scale + fade), uses a native `NSVisualEffectView` material with the panel's appearance forced to the app theme (so it never clashes with a light menu bar), hugs its text as a capsule, and carries the state in a coloured dot (red / amber / blue) instead of an emoji.
- **Launch at login** via `SMAppService` (macOS 13+), reconciled to the real OS registration status on launch so the toggle never silently drifts.
- Speech-profiles user guide: `docs/speech-profiles.md`.
- Dev tool `scripts/seg_replay.py` — replays saved recordings through the segmenter to tune pause cutting against real audio.

### Fixed
- **Streaming silently ignored a dead recognition server.** When whisper went down mid-take, the HUD kept showing "listening" and the failure only surfaced at Stop — you'd keep talking to nothing. Now the first failed segment immediately flips the HUD to a red "server unavailable" state and fires a single notification (re-armed once a segment succeeds), so an outage is visible at once. The full clip is still saved for re-transcription.
- **whisper-server crash report on every quit.** The server's own signal handler calls `exit()`, which aborts inside the Metal teardown (`ggml_metal_rsets_free` → `ggml_abort` → SIGABRT), filing a crash report each time it was stopped. `scripts/start.sh` now stops it with SIGKILL (it's stateless, nothing to flush) so it dies cleanly, and launches it under `nohup` so an incidental SIGHUP (terminal/login session ending on sleep or logout) can't tear it down mid-dictation.
- **Stuck Command modifier after paste.** Flag-only Cmd+V synthesis left Command logically held (beeps, double-clicks, Space → Spotlight). `_press_cmd_v` now presses ⌘ as a real key with an explicit ⌘-up, from an isolated private event source.
- **Paste landing in Spotlight.** A Spotlight/Siri overlay floats over the frontmost app and eats the keyboard, so a frontmost-pid check passes but Cmd+V lands in the overlay. Now detected via the on-screen window list; the text is left on the clipboard with a notification instead.
- **AX paste falsely reporting success in Electron (Claude).** `AXSelectedText` returns `err=0` even when the write lands nowhere. The AX path now verifies by reading the value back and falls back to Cmd+V when unconfirmed.
- **Launch-at-login silently failing.** `ServiceManagement` was missing from the venv, so registration failed quietly and the toggle reset. Added `pyobjc-framework-ServiceManagement` and a real-status sync.

### Fixed (earlier)
- Crash (`Abort trap: 6` / NSException) when the transcription "too short" branch fired. Tray UI updates (`set_title`, `set_status`, checkmark refresh) were happening on background threads (CGEventTap CFRunLoop, `_finish` daemon, whisper health check); AppKit requires `NSStatusItem` / `NSMenuItem` mutations on the main thread. Now wrapped in `PyObjCTools.AppHelper.callAfter`.

### Added
- Platform abstraction layer (`src/backend/`) with Protocol contracts.
- pyproject.toml, ruff config, pytest config.
- LICENSE (MIT), CONTRIBUTING, CHANGELOG.
- GitHub Actions CI: lint + smoke imports on macOS.
- «🌍 Languages» submenu with 17 languages (Thai and Vietnamese added; total 18 modes including the flagship `→ English` shortcut).

### Changed
- Project restructure: source moved into `src/`, mapped to the `cream_typer` package via setuptools `package-dir`.
- Install via `pip install -e .[macos,dev]` instead of a raw `requirements.txt`.
- All UI strings, comments, and documentation translated to English (language picker labels remain as endonyms).

## [0.1.0] — 2026-04-26

### Added
- First public release.
- Caps Lock toggle via CGEventTap (macOS).
- Whisper.cpp HTTP client for local transcription.
- Bidirectional translation by swapping `language` (sidesteps the broken `task=translate` on large-v3-turbo).
- Previous clipboard contents are saved and restored after Cmd+V.
- `make setup` — clones, builds whisper.cpp, and downloads the model in a single command.
