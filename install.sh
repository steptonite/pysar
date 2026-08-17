#!/bin/bash
# One-command install for Pysar.
#
# From a clean Mac, with nothing checked out:
#   curl -fsSL https://raw.githubusercontent.com/steptonite/pysar/main/install.sh | bash
#
# Or, from inside a clone:
#   ./install.sh
#
# It is idempotent — safe to re-run to update an existing install. It:
#   1. checks this is an Apple-Silicon Mac with the Xcode command-line tools,
#   2. makes sure Homebrew + cmake + git + a Python ≥3.10 + ffmpeg are present
#      (installs the missing ones via Homebrew; ffmpeg is needed to
#      transcribe audio/video files, not for live dictation),
#   3. clones the repo if you ran it via curl (skips if already in a clone),
#   4. runs `make setup` (venv + whisper.cpp + speech & VAD models),
#   5. runs `make app` (the menu-bar app into /Applications + the `pysar` alias).
#
# ── Why the whole script lives inside main() ──────────────────────────────────
# Under `curl … | bash` the script IS bash's stdin, and bash reads it in chunks:
# it executes what it has read, then comes back for more. Any child process that
# reads stdin (Homebrew does, when it prompts) swallows the rest of the script,
# and the install stops halfway with no error — dependencies installed, Pysar
# never built. That is exactly what happened on a tester's Mac on 17.08.2026.
# Defining one big function forces bash to read the file to the closing brace
# before it runs a single command, so the pipe is fully drained first; every
# child then gets stdin from /dev/null so nothing can eat it anyway.
set -euo pipefail

main() {
REPO_URL="https://github.com/steptonite/pysar.git"
CLONE_DIR="${PYSAR_DIR:-$HOME/code/pysar}"
MIN_PY_MINOR=10  # pysar needs CPython ≥3.10 (match-case, PEP 604 unions)

say()  { printf "\033[1m%s\033[0m\n" "$1"; }
warn() { printf "\033[33m⚠️  %s\033[0m\n" "$1" >&2; }
die()  { printf "\033[31m❌ %s\033[0m\n" "$1" >&2; exit 1; }

# ── 1. Platform check ─────────────────────────────────────────────────────
[ "$(uname -s)" = "Darwin" ]  || die "macOS only."
[ "$(uname -m)" = "arm64" ]   || die "Apple Silicon (M-series) only — Metal acceleration is required."

# Checked here rather than in scripts/install_app.sh, which needs clang only at
# the very END: without this you sat through the whole whisper.cpp build and the
# 1.5 GB model download before being told to install the command-line tools.
if ! xcode-select -p >/dev/null 2>&1 || ! command -v clang >/dev/null 2>&1; then
    say "🛠  Xcode command-line tools are missing — asking macOS to install them…"
    xcode-select --install >/dev/null 2>&1 || true
    die "Finish the 'Command Line Tools' installer macOS just opened, then re-run this script."
fi

# ── 2. Prerequisites (Homebrew + cmake + git + python ≥3.10 + ffmpeg) ───────
if ! command -v brew >/dev/null 2>&1; then
    say "🍺 Homebrew not found — installing it…"
    NONINTERACTIVE=1 /bin/bash -c \
        "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/null
    # Make brew available in this shell for the rest of the run.
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$b" ] && eval "$("$b" shellenv)" && break
    done
    command -v brew >/dev/null 2>&1 || die "Homebrew installed but 'brew' is still not on PATH — open a new terminal and re-run."
fi

# Is there ALREADY an interpreter new enough? `command -v python3` is not an
# answer: macOS ships /usr/bin/python3 = 3.9.6, which passed the old check and
# then failed at build time with a confusing error (tester, 17.08.2026).
py_ok() {  # py_ok <interpreter> → true when it is CPython ≥ 3.<MIN_PY_MINOR>
    "$1" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, $MIN_PY_MINOR) else 1)" \
        >/dev/null 2>&1
}
# Same order as scripts/find_python.sh (duplicated because this runs BEFORE the
# clone exists): 3.12 first — the version Pysar is tested on — not the newest,
# whose PyObjC wheels may not exist yet.
find_python() {
    local cand name
    for name in python3.12 python3.13 python3.11 python3.10 python3.14 python3; do
        cand="$(command -v "$name" 2>/dev/null || true)"
        [ -n "$cand" ] && py_ok "$cand" && { printf '%s' "$cand"; return 0; }
        cand="/opt/homebrew/bin/$name"
        [ -x "$cand" ] && py_ok "$cand" && { printf '%s' "$cand"; return 0; }
    done
    return 1
}

need_brew=()
command -v cmake  >/dev/null 2>&1 || need_brew+=("cmake")
command -v git    >/dev/null 2>&1 || need_brew+=("git")
command -v ffmpeg >/dev/null 2>&1 || need_brew+=("ffmpeg")
PYSAR_PY="$(find_python || true)"
if [ -z "$PYSAR_PY" ]; then
    say "🐍 No Python ≥3.$MIN_PY_MINOR found (macOS only ships 3.9) — installing python@3.12."
    need_brew+=("python@3.12")
fi
if [ "${#need_brew[@]}" -gt 0 ]; then
    say "📦 Installing: ${need_brew[*]}"
    # </dev/null: a brew prompt must never reach for the pipe this script may be
    # arriving on. NONINTERACTIVE makes brew skip the prompts entirely.
    NONINTERACTIVE=1 brew install "${need_brew[@]}" </dev/null
fi
if [ -z "$PYSAR_PY" ]; then
    hash -r
    PYSAR_PY="$(find_python || true)"
    [ -n "$PYSAR_PY" ] || die "Homebrew finished but no Python ≥3.$MIN_PY_MINOR is on PATH. Try: brew link --overwrite python@3.12"
fi
say "🐍 Python: $PYSAR_PY ($("$PYSAR_PY" -c 'import platform;print(platform.python_version())'))"

# Bring an existing clone up to date. Not a bare `git pull --ff-only`: when
# someone debugs a broken install they patch files in place (a helper disabled
# Metal in the tester's clone on 17.08.2026), and a dirty tree makes the pull
# fail. The old code warned once and then quietly built the STALE checkout —
# so "re-run the installer to update" silently did nothing. Local work is parked
# in the stash (never discarded) and the failure modes are spelled out.
update_clone() {
    local root="$1" dirty=""
    dirty="$(git -C "$root" status --porcelain --untracked-files=no 2>/dev/null || true)"
    if [ -n "$dirty" ]; then
        warn "this clone has local edits — parking them in the stash so the update can land:"
        printf '%s\n' "$dirty" | sed 's/^/      /' >&2
        if git -C "$root" stash push -m "pysar-install $(date '+%Y-%m-%d %H:%M')" </dev/null >/dev/null 2>&1; then
            say "   ↩︎ get them back any time with:  git -C $root stash pop"
        else
            warn "couldn't stash them — building whatever is checked out."
            return 0
        fi
    fi
    if ! git -C "$root" pull --ff-only </dev/null; then
        warn "this clone has diverged from origin/main, so it can't fast-forward."
        warn "Building the checked-out version. To take the latest instead, run:"
        warn "    git -C $root fetch origin && git -C $root reset --hard origin/main"
    fi
}

# ── 3. Locate or clone the repo ─────────────────────────────────────────────
# If this script lives inside a clone (has a Makefile next to it), use that.
# Piped into bash, BASH_SOURCE is empty and $0 is "bash" — SCRIPT_DIR then
# resolves to the CWD, which is why the Makefile grep below has to confirm it.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd)"
if [ -f "$SCRIPT_DIR/Makefile" ] && grep -q "pysar" "$SCRIPT_DIR/Makefile" 2>/dev/null; then
    ROOT="$SCRIPT_DIR"
    if [ -z "${BASH_SOURCE[0]:-}" ]; then
        # Piped in (the curl one-liner) while standing in a clone: the user asked
        # for the published version, so fetch it. Running ./install.sh from the
        # file is the opposite intent — a developer building their working tree —
        # and must never move it under them.
        say "📂 In a clone at $ROOT — updating to the published version…"
        update_clone "$ROOT"
    else
        say "📂 Using this clone at $ROOT (your working tree, left as it is)"
    fi
elif [ -d "$CLONE_DIR/.git" ]; then
    ROOT="$CLONE_DIR"
    say "📂 Found existing clone at $ROOT — updating…"
    update_clone "$ROOT"
else
    say "📥 Cloning $REPO_URL → $CLONE_DIR"
    mkdir -p "$(dirname "$CLONE_DIR")"
    git clone "$REPO_URL" "$CLONE_DIR" </dev/null
    ROOT="$CLONE_DIR"
fi
cd "$ROOT"

# A venv left behind by a half-finished run can be built on the wrong (3.9)
# interpreter; `make setup` would then reuse it and fail deep inside pip.
if [ -x "venv/bin/python" ] && ! py_ok "venv/bin/python"; then
    warn "existing venv/ uses Python $(venv/bin/python -c 'import platform;print(platform.python_version())' 2>/dev/null || echo '?') — rebuilding it on $PYSAR_PY"
    rm -rf venv
fi

# ── 4 + 5. Build everything ──────────────────────────────────────────────────
say "🔧 make setup  — venv + whisper.cpp + speech & VAD models (one-time, a few minutes)…"
make setup PYTHON="$PYSAR_PY" </dev/null
say "📦 make app    — installing the menu-bar app into /Applications…"
make app </dev/null

cat <<DONE

✅ Pysar is installed.
   • Launch it from Spotlight → "Pysar" (or run: pysar)
   • First run: grant Input Monitoring + Accessibility to Pysar
     in System Settings → Privacy & Security, then relaunch.
   • Dictate: tap Caps Lock, speak, tap again.
DONE
}

main "$@"
