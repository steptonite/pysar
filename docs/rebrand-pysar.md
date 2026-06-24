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

## Phase 3 — app bundle + infra cosmetics ✅ (commit `b15bf67`)

**Done:** `scripts/install_app.sh` rewritten — builds `/Applications/Pysar.app`
(`CFBundleName`/`DisplayName`=Pysar, `CFBundleExecutable`=pysar,
`CFBundleIconFile`=Pysar, mic-usage text), and now also `rm -rf` the old
`Pysar.app`. Env `PYSAR_PYTHON`/`PYSAR_SITE` → `PYSAR_PYTHON`/`PYSAR_SITE`
across `install_app.sh` / `start.sh` / `_app_main.py`. Logs `/tmp/cream-whisper.log`
→ `/tmp/pysar-whisper.log` (`start.sh`, `src/server.py`) and `~/Library/Logs/pysar.log`
→ `pysar.log`. Icon asset `git mv assets/Pysar.icns → Pysar.icns`; updated
`Makefile` icon target **and the two code paths that load it**
(`src/backend/_macos.py`, `src/backend/settings_window.py`). Alias `cream`→`pysar`;
`install.sh` `CREAM_DIR`→`PYSAR_DIR`, comments.
**Verified:** `make test` → 128 passed; `make app` clean; `/Applications/Pysar.app`
gone, `Pysar.app` present; plist DisplayName/Executable/IconFile = Pysar (id kept
`com.steptonite.pysar`); launched → whisper server up in 3 s, process runs from
`Pysar.app`, logs land in the new paths.
**Deliberately left:** Bundle ID (Phase 4); data dir `Application Support/Pysar`
+ `cream.log` (Phase 5); upstream attribution (`pyproject.urls`, LICENSE, README,
`_macos.py` upstream comment); internal WebKit JS bridge names `pysarApply`/`pysarBridge`
(not brand-facing — renaming risks breaking the native↔JS bridge for zero user benefit);
`make_icon.py` "cream"/"amber-cream" = colour names, not the brand.
**Can break:** an old `alias cream=` may still sit in `~/.zshrc` (harmless — still
runs `make up`); GitHub repo `pysar-custom` not renamed so `REPO_URL` is unchanged.

### original plan

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
