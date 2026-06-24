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

## Phase 2 — Python package `pysar` → `pysar` ✅ (commit `5c67225`)

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

## Phase 3 — app bundle + infra cosmetics ✅ (commit `a351120`)

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
runs `make up`).

### Post-phase follow-ups (after the 5 phases)

- **GitHub repo renamed** `steptonite/pysar-custom` → `steptonite/pysar`
  (`gh repo rename`); `origin` remote + `REPO_URL`/curl in `install.sh` + README
  updated; `CLONE_DIR` default → `$HOME/code/pysar` (fresh installs only).
- **Local working dir kept** as `~/code/pysar` on purpose: the in-repo
  `venv/` bakes absolute paths into its console-script shebangs + `activate`, and
  the installed `.app` launcher bakes an absolute `ROOT` — moving the folder breaks
  both. Renaming it would need a `make setup` venv rebuild + `make app`; not worth
  it for a cosmetic local name.
- **Icon redesigned** (`scripts/make_icon.py`): pen-nib (scribe) on a slate-ink
  squircle with one ochre accent — replaces the old pysar mic/cream colours.
  Regenerated `assets/Pysar.icns`, rebuilt the `.app`.
- **No-Dock-icon is by design:** the app is `LSUIElement` (menu-bar agent), so it
  has no permanent Dock tile or ⌘-Tab entry; the menu bar shows the language flag.
  The `.icns` surfaces in Finder/Spotlight/Settings window.

### original plan

---

## Phase 4 — Bundle ID ✅ (commit `4162dca`)

**Done:** `CFBundleIdentifier` `com.steptonite.pysar` → `com.steptonite.pysar`
(own namespace, drops the upstream steptonite one) in `scripts/install_app.sh`; NOTE
comment updated. No other refs to the old id in code/tests. Rebuilt + re-registered
via `lsregister -f`, relaunched.
**Verified (by me):** plist id = `com.steptonite.pysar`; app runs from `Pysar.app`;
whisper server up in 3 s. TCC.db not introspectable without Full Disk Access.
**Needs YOU to verify + act:** macOS keys Input Monitoring / Accessibility by bundle
id, so they almost certainly need re-granting:
  1. System Settings → Privacy & Security → **Input Monitoring** → enable **Pysar**
     (add with `+` → /Applications/Pysar.app if absent); remove stale "Pysar"/"Python".
  2. Same under **Accessibility**.
  3. Relaunch Pysar, test Caps Lock dictation + paste.
**Can break:** dictation silently dead until perms re-granted (expected).

---

## Phase 5 — data folder migration ✅ (commit `3120367`)

**Done:** new dependency-free `src/paths.py` with `data_dir()` — one-time atomic
rename of `Application Support/Pysar` → `Pysar` (guarded: old exists AND new
absent), preserving `settings.json` + `recordings/` as a unit. Wired into
`src/logsetup.py` (log now `pysar.log`, "Pysar start" banner), `src/recordings.py`
(`_BASE = data_dir()`), `scripts/seg_replay.py` (`REC_DIR = data_dir()/recordings`).
**Verified on the real machine:** migration ran — old `Pysar` dir gone, `Pysar`
dir holds the same `settings.json` (17318 bytes, unchanged) + all 10 recordings;
ruff clean; `make test` → 128 passed; relaunched, new `pysar.log` written, hotkey
listener started. The old `cream.log` rode along inside the moved folder as a
harmless orphan (new logs go to `pysar.log`).
**Can break:** nothing outstanding — migration is idempotent (guard no-ops once
`Pysar` exists). A fresh install with no old folder just creates `Pysar` on first write.
