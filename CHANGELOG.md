# Changelog

All notable changes to this project are tracked in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
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
