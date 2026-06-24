#!/bin/bash
# One-command install for Pysar.
#
# From a clean Mac, with nothing checked out:
#   curl -fsSL https://raw.githubusercontent.com/steptonite/cream-typer-custom/main/install.sh | bash
#
# Or, from inside a clone:
#   ./install.sh
#
# It is idempotent — safe to re-run to update an existing install. It:
#   1. checks this is an Apple-Silicon Mac,
#   2. makes sure Homebrew + cmake + git + python are present (installs the
#      missing ones via Homebrew),
#   3. clones the repo if you ran it via curl (skips if already in a clone),
#   4. runs `make setup` (venv + whisper.cpp + speech & VAD models),
#   5. runs `make app` (the menu-bar app into /Applications + the `cream` alias).
set -euo pipefail

REPO_URL="https://github.com/steptonite/cream-typer-custom.git"
CLONE_DIR="${CREAM_DIR:-$HOME/code/cream-typer}"

say()  { printf "\033[1m%s\033[0m\n" "$1"; }
die()  { printf "\033[31m❌ %s\033[0m\n" "$1" >&2; exit 1; }

# ── 1. Platform check ─────────────────────────────────────────────────────
[ "$(uname -s)" = "Darwin" ]  || die "macOS only."
[ "$(uname -m)" = "arm64" ]   || die "Apple Silicon (M-series) only — Metal acceleration is required."

# ── 2. Prerequisites (Homebrew + cmake + git + python) ──────────────────────
if ! command -v brew >/dev/null 2>&1; then
    say "🍺 Homebrew not found — installing it…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Make brew available in this shell for the rest of the run.
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

need_brew=()
command -v cmake  >/dev/null 2>&1 || need_brew+=("cmake")
command -v git    >/dev/null 2>&1 || need_brew+=("git")
command -v python3 >/dev/null 2>&1 || need_brew+=("python@3.12")
if [ "${#need_brew[@]}" -gt 0 ]; then
    say "📦 Installing: ${need_brew[*]}"
    brew install "${need_brew[@]}"
fi

# ── 3. Locate or clone the repo ─────────────────────────────────────────────
# If this script lives inside a clone (has a Makefile next to it), use that.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd)"
if [ -f "$SCRIPT_DIR/Makefile" ] && grep -q "cream_typer" "$SCRIPT_DIR/Makefile" 2>/dev/null; then
    ROOT="$SCRIPT_DIR"
    say "📂 Using existing clone at $ROOT"
elif [ -d "$CLONE_DIR/.git" ]; then
    ROOT="$CLONE_DIR"
    say "📂 Found existing clone at $ROOT — updating…"
    git -C "$ROOT" pull --ff-only || true
else
    say "📥 Cloning $REPO_URL → $CLONE_DIR"
    mkdir -p "$(dirname "$CLONE_DIR")"
    git clone "$REPO_URL" "$CLONE_DIR"
    ROOT="$CLONE_DIR"
fi
cd "$ROOT"

# ── 4 + 5. Build everything ──────────────────────────────────────────────────
say "🔧 make setup  — venv + whisper.cpp + speech & VAD models (one-time, a few minutes)…"
make setup
say "📦 make app    — installing the menu-bar app into /Applications…"
make app

cat <<DONE

✅ Pysar is installed.
   • Launch it from Spotlight → "Pysar" (or run: cream)
   • First run: grant Input Monitoring + Accessibility to Pysar
     in System Settings → Privacy & Security, then relaunch.
   • Dictate: tap Caps Lock, speak, tap again.
DONE
