#!/bin/bash
# Build "/Applications/Pysar.app" — a Dock-less menu-bar launcher that
# starts the whisper server and the app with one click (Spotlight/Launchpad).
# Also installs a `pysar` shell alias. Idempotent: safe to re-run.
#
# Everything it needs lives in the repo, so a fresh Mac just does:
#   make setup && make app
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="/Applications/Pysar.app"
ICNS="$ROOT/assets/Pysar.icns"
VENV_PY="$ROOT/venv/bin/python"

echo "📦 Installing $APP (repo: $ROOT)"

# Resolve the framework python binary + the venv's site-packages. We ship a COPY
# of the binary inside the bundle (below) so NSBundle.mainBundle() resolves to
# this .app — the only thing that actually makes the Dock/⌘-Tab show our name and
# icon instead of "Python". A symlink does NOT work (macOS resolves it back to the
# framework's own Python.app); setting __CFBundleIdentifier does NOT work either.
if [ ! -x "$VENV_PY" ]; then
    echo "❌ venv not found at $VENV_PY — run 'make setup' first."; exit 1
fi
PY_SRC="$("$VENV_PY" -c 'import sys,os;print(os.path.join(sys.base_prefix,"Resources/Python.app/Contents/MacOS/Python"))')"
SITE_DIR="$("$VENV_PY" -c 'import site;print(site.getsitepackages()[0])')"

# Remember the identity of the copy we're replacing (see the TCC block at the end).
OLD_CDHASH="$(codesign -dvvv "$APP" 2>&1 | awk -F= '/^CDHash/{print $2}' || true)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ── Info.plist (LSUIElement = menu-bar agent, no Dock icon) ───────────────────
# NOTE: macOS keys TCC permissions by bundle id, so a change to
# CFBundleIdentifier needs Input Monitoring + Accessibility re-granted to Pysar
# in System Settings → Privacy & Security.
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Pysar</string>
    <key>CFBundleDisplayName</key><string>Pysar</string>
    <key>CFBundleIdentifier</key><string>com.steptonite.pysar</string>
    <key>CFBundleExecutable</key><string>pysar</string>
    <key>CFBundleIconFile</key><string>Pysar</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>LSUIElement</key><true/>
    <key>NSMicrophoneUsageDescription</key><string>Pysar records your voice locally to transcribe it into text.</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# ── Bundled python (a real copy → mainBundle resolves to THIS .app) ───────────
if [ -x "$PY_SRC" ]; then
    cp "$PY_SRC" "$APP/Contents/MacOS/Python"
    chmod +x "$APP/Contents/MacOS/Python"
else
    echo "⚠️  python binary not found at $PY_SRC — Dock will fall back to 'Python'."
fi

# ── Launcher (compiled — a shell script here makes bash the app's main binary,
# so TCC never sees one stable "Pysar" identity; see scripts/app_launcher.c) ───
if ! command -v clang >/dev/null; then
    echo "❌ clang not found — install the Xcode Command Line Tools (xcode-select --install)."
    exit 1
fi
clang -O2 -Wall \
    -DPYSAR_ROOT="\"$ROOT\"" \
    -DPYSAR_SITE="\"$SITE_DIR\"" \
    -o "$APP/Contents/MacOS/pysar" "$ROOT/scripts/app_launcher.c"

# ── Icon ──────────────────────────────────────────────────────────────────────
if [ -f "$ICNS" ]; then
    cp "$ICNS" "$APP/Contents/Resources/Pysar.icns"
else
    echo "⚠️  $ICNS missing — app will use the generic icon. Run: make icon"
fi

# ── Ad-hoc codesign (whole bundle, launcher + bundled Python) ─────────────────
# Without a Developer ID this is the best available: TCC gets a signed, single
# app identity instead of "not signed at all". The cdhash changes on every
# rebuild, so re-running this script means re-granting permissions once — same
# cost as before, but grants now stick between launches.
codesign --force --deep --sign - "$APP"

# ── Drop the previous copy's dead TCC grants ─────────────────────────────────
# Without a Developer ID the ad-hoc cdhash changes whenever the bundle is
# rebuilt, and macOS then ignores every permission granted to the old identity —
# but keeps SHOWING it as granted. The user sees Accessibility and Input
# Monitoring switched on while nothing works, and no amount of toggling helps
# (a tester lost an evening to exactly this on 17.08.2026; only a manual reset
# fixed it). Clearing the stale rows here means macOS asks again, honestly.
# Only fires when the identity actually changed — a rebuild that produces the
# same cdhash keeps its grants, and a first install has nothing to clear.
NEW_CDHASH="$(codesign -dvvv "$APP" 2>&1 | awk -F= '/^CDHash/{print $2}' || true)"
BUNDLE_ID="com.steptonite.pysar"
if [ -n "$OLD_CDHASH" ] && [ "$OLD_CDHASH" != "$NEW_CDHASH" ]; then
    echo "🔐 the app's signature changed — clearing the old copy's stale permissions…"
    for svc in Accessibility ListenEvent Microphone ScreenCapture; do
        tccutil reset "$svc" "$BUNDLE_ID" >/dev/null 2>&1 || true
    done
    echo "   → grant Input Monitoring + Accessibility to Pysar again on first launch."
fi

# ── Register + refresh icon cache ─────────────────────────────────────────────
plutil -lint "$APP/Contents/Info.plist" >/dev/null
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$APP" || true
touch "$APP"

# ── `pysar` alias (launch from any terminal) ──────────────────────────────────
ZRC="$HOME/.zshrc"
if ! grep -q "alias pysar=" "$ZRC" 2>/dev/null; then
    printf '\n# Pysar voice dictation\nalias pysar="make -C %s up"\n' "$ROOT" >> "$ZRC"
    echo "🔗 added 'pysar' alias to ~/.zshrc"
fi

echo "✅ Done. Launch via Spotlight → “Pysar”, or run 'pysar' in a new terminal."
echo "   First run: grant Input Monitoring + Accessibility to Pysar in System Settings."
