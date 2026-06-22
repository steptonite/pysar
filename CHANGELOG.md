# Changelog

All notable changes to this project are tracked in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **One-command install** — `install.sh` (a `curl … | bash` one-liner) bootstraps a clean Mac end-to-end: checks for Apple Silicon, installs missing prerequisites via Homebrew (`cmake`, `git`, Python), clones the repo if needed, then runs `make setup` + `make app`. Idempotent (re-run to update). Also added a `make all` target (`setup` + `app`). README install collapsed to the single command.
- **Streaming dictation mode** (opt-in via Settings → Dictation). Types each sentence into the focused field as you speak instead of one transcription at the end. New `src/segmenter.py` cuts audio on natural pauses (adaptive RMS noise floor, never mid-word; hard `MAX_SEG_SEC` cap for pause-free run-ons). A serialized queue worker transcribes and types segments one at a time so word order holds and whisper is never run concurrently on 8 GB. Sentences are inserted as synthetic Unicode key events (`Paster.type_text`) — no clipboard involvement, so the user's clipboard is never clobbered mid-dictation. Batch remains the default. Tunable params in `src/config.py` (`PAUSE_SEC`, `MIN_SEG_SEC`, `MAX_SEG_SEC`, `SILENCE_MARGIN`).
- **Focus-aware buffering for streaming.** Before typing each sentence, `Paster.has_editable_focus()` checks the keyboard focus is a real text field (Spotlight/Siri overlay or a non-editable target ⇒ no field; same-app-as-start ⇒ trusted, to protect the Electron/Claude path). On loss it latches **buffer mode**: the rest of the take is collected and placed on the clipboard in one piece at Stop with a single notification — nothing is typed blind into the wrong place.
- **Floating status HUD** (`src/backend/_hud.py`) that drops out of the app's own menu-bar icon, showing listening / recognizing / buffering. A non-activating, click-through `NSPanel` that never steals focus; failures are swallowed so a missing overlay never breaks dictation. It anchors under the status-bar button, springs in on first show (scale + fade), uses a native `NSVisualEffectView` material with the panel's appearance forced to the app theme (so it never clashes with a light menu bar), hugs its text as a capsule, and carries the state in a coloured dot (red / amber / blue) instead of an emoji.
- **Launch at login** via `SMAppService` (macOS 13+), reconciled to the real OS registration status on launch so the toggle never silently drifts.
- Speech-profiles user guide: `docs/speech-profiles.md`.
- Dev tool `scripts/seg_replay.py` — replays saved recordings through the segmenter to tune pause cutting against real audio.

### Fixed
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
