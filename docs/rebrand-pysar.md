# Rebrand: pysar → Pysar — process log

Staged rename so a breakage is isolated to one phase. Each phase: what changed,
what was verified, and where it can still break. The risky phases (Bundle ID,
data folder) are deliberately last and separate.

**Must stay `pysar` (upstream MIT attribution — do not touch):**
`LICENSE` (Copyright Pysar), README/CONTRIBUTING links to `steptonite/pysar`,
the upstream URLs in `pyproject.toml [project.urls]`.

---

## Phase 1 — display strings + docs ✅ (commit `b3db47d`)

User-facing strings and docs now say **Pysar**.

- Changed: `README.md` (title + body), `src/i18n.py`, `src/app.py`,
  `src/backend/settings_window.py`, `src/backend/_macos.py`, `src/__init__.py`.
- Verified: grep shows Pysar in menu bar / notifications / Settings window / title.
- Can break: nothing structural — pure strings.

---

## Phase 2 — Python package `pysar` → `pysar` ✅ (commit `77febf4`)

**Done:** `pyproject.toml` (`name`, `packages`, `package-dir`, `[project.scripts]`
`pysar = "pysar.app:main"`, ruff `known-first-party`); all `tests/*` imports;
`python -m pysar` in `Makefile` + `scripts/start.sh`; `scripts/_app_main.py`
(`runpy.run_module("pysar")`); `scripts/seg_replay.py`; `.github/workflows/ci.yml`;
`install.sh` Makefile-detection grep; docstrings in `src/__main__.py` / `src/app.py`.
Left historical: `CHANGELOG.md` line, design-spec memory-slug reference.
**Verified:** `pip install -e .` clean, `from pysar import ...` + `from pysar.backend import ...` OK, `make test` → 128 passed.
**Can still break:** the `.app` launcher (Phase 3) must call `python -m pysar` — until rebuilt, the installed `.app` still runs the old module name via its bundled launcher.

### original plan

Scope: the import name only. Code lives in `src/` via relative imports, so this is
the setuptools mapping + console-script + test imports + `python -m`, NOT a dir move.

- To change: `pyproject.toml` (`packages`, `package-dir`, `[project.scripts]`,
  ruff `known-first-party`), all `tests/*` imports `pysar.*` → `pysar.*`,
  `python -m pysar` in `Makefile` / `scripts/start.sh`, docstrings in
  `src/__main__.py` / `src/app.py`.
- Reinstall editable (`pip install -e .`) so the new mapping takes effect; old
  `pysar.egg-info` is stale and regenerates.
- Verify: `make test` green; `python -m pysar --help`/import works.
- Can break: any missed `pysar` import → ImportError at launch; the `.app`
  still launches via `scripts/start.sh` which must point at `python -m pysar`.

---

## Phase 3 — app bundle + infra cosmetics ⏳

`.app` display name, executable, icon, shell alias, env vars, log files.
**Bundle ID and the data folder are intentionally NOT touched here.**

- `.app`: `Pysar.app` → `Pysar.app`; `CFBundleName`/`DisplayName`
  `Pysar Custom` → `Pysar`; `CFBundleExecutable` `pysar` → `pysar`;
  icon `Pysar.icns` → `Pysar.icns`; `NSMicrophoneUsageDescription` text.
- alias `cream` → `pysar`; env `PYSAR_PYTHON`/`PYSAR_SITE` → `PYSAR_*`;
  logs `/tmp/cream-whisper.log` + `pysar.log` → `pysar` names.
- `install.sh` `CLONE_DIR`/`CREAM_DIR`. (GitHub repo rename is a manual action —
  `REPO_URL` left until the repo itself is renamed; flagged, not broken.)
- Rebuild `.app` (`make app`), relaunch, confirm menu bar + Dock/⌘-Tab say Pysar.
- Can break: stale old `.app` left in /Applications; alias duplicate in `.zshrc`.

---

## Phase 4 — Bundle ID ⏳ (RISKY — isolated on purpose)

`CFBundleIdentifier` `com.steptonite.pysar` → `com.steptonite.pysar`.

- ⚠️ macOS keys TCC permissions by bundle ID → **Input Monitoring + Accessibility
  must be granted again** to the new Pysar.app on first launch.
- Verify: launch, re-grant perms, confirm Caps Lock dictation + paste work.
- Can break: dictation silently dead until perms re-granted (expected, documented).

---

## Phase 5 — data folder migration ⏳ (RISKY — isolated on purpose)

`~/Library/Application Support/Pysar/` → `Pysar/` (logs + recordings).

- Add one-time migration: if old dir exists and new doesn't, rename it; else create new.
- Update `src/logsetup.py`, `src/recordings.py`, `scripts/seg_replay.py`.
- Verify: existing recordings still visible via the menu; new logs land in Pysar/.
- Can break: orphaned recordings if migration skipped — migration guard prevents data loss.
