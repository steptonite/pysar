# Autostart + capture-teardown fixes — process log

Two independent fixes, each isolated so a breakage is locatable. Both land in the
dictation/meeting code (not the uncommitted meeting-settings UI work).

---

## Fix A — Launch-at-login no longer "enabled but doesn't start" ✅

**Symptom (user):** toggled Launch-at-login on, rebooted, Pysar did not start; the
stored `launch_at_login` had silently reset to `False`.

**Root cause:** Pysar is unsigned, so `SMAppService.register()` lands the login
item in **`RequiresApproval`** (registered, but macOS needs a one-time confirm),
not `Enabled`. The old `login_item_enabled()` treated *only* `Enabled` as on, so on
the next launch `_initial_launch_at_login` saw "not enabled" and reset the
checkmark/settings to off — the item also never actually launched because it was
never approved. The bundle-id change (`com.steptonite.pysar`) + repeated `.app`
rebuilds during the rebrand had orphaned any earlier registration too.

**Done (`_macos.py`):**
- New `login_item_status()` → `"enabled" | "requires_approval" | "not_registered"
  | "not_found" | "unknown" | None`.
- New `open_login_items_settings()` → `SMAppService.openSystemSettingsLoginItems()`.
- `set_login_item(True)`: after register, if status is `requires_approval`, deep-link
  to Login Items and return `True` (registration is real, approval pending).
- `login_item_enabled()` now counts `requires_approval` as on → the checkmark
  reflects intent and no longer drifts off.
- `Tray._set_login`: on `requires_approval`, notify "Approve Pysar in Login Items"
  (`notif.loginApprove` / `…Body`, added to both i18n tables).

**Verified:** ruff clean; `_macos.py` parses; i18n parity holds + new keys present;
`pytest` → 128 passed; app relaunched clean (loads the new code).
**Live acceptance (user):** relaunch Pysar → toggle Launch-at-login on → the Login
Items pane opens → switch Pysar on there → reboot and confirm it starts. The menu
checkmark should now persist across relaunch.
**Can break:** all SMAppService calls are exception-guarded; if the framework can't
be read, `login_item_enabled()` returns `None` and the caller keeps the stored
value (no drift). Still requires running as the installed `.app` (not a terminal
run), as before.

---

## Fix B — capture teardown can no longer leak the mic ✅

**Symptom (user):** the system-capture share/permission flow hung; the mic stayed
held (AirPods dropped to hands-free, low quality) and only a reboot freed it.

**Root cause:** `SystemAudioRecorder.start()` kicks off
`getShareableContentWithCompletionHandler_`; the completion (`_on_content`) builds
and starts the `SCStream` asynchronously on the main queue. If `stop()` ran before
that completion (or during the async start), `stop()` read `self._stream` as `None`
and did nothing — then `_on_content` finished and started a stream **nobody held a
reference to**, keeping the mic open with no way to stop it.

**Done (`syscap.py`):**
- `stop()` sets `self._stopped` **first** (closing the start-after-stop race), then
  tears down the stream and nils `_output`/`_queue`.
- `_on_content()` bails immediately if `self._stopped` is set (before opening
  anything), and re-checks right before `startCapture…` — tearing down a built but
  unstarted stream instead of starting it.
- The `started` completion also re-checks `self._stopped` and stops the stream
  immediately if stop raced in during the async start.

**Verified:** ruff clean; `syscap.py` parses; `pytest` → 128 passed.
**Can break:** the mic is only ever opened by `startCaptureWithCompletionHandler_`,
which is now guarded on both sides of the await; teardown calls are
exception-suppressed so a stop can't throw. (Does not change the steady-state
capture path — only the start/stop edges.)

**Still open (separate):** the *picker/permission-dialog hang* itself isn't
prevented — this fix guarantees a clean release when stop is called, so a hang no
longer requires a reboot. Hardening the first-run permission flow (so it can't hang
the run loop at all) is a later task.

---

## Fix C — mic recovers without an app restart after the device was held ✅

**Symptom (user, 25.06 morning):** overnight a *leaked* ScreenCaptureKit capture
(`replayd`, started 02:10) held the mic for ~9h. The menu showed
`Error opening InputStream: Internal PortAudio error [PaError…]`. whisper-server
was actually alive the whole time (up since 02:22, :8080 → 200) — the user read
"server died" but the real blocker was the mic. Killing the leaked `replayd`
freed the device (a fresh Python process opened the input fine), **but the
already-running app still failed** — and the user deliberately did not restart it.

**Root cause:** PortAudio is initialized once per process and caches the device
list + HAL state at `Pa_Initialize` time. The app started (02:22) while the device
was still held, so its PortAudio context was poisoned. `_open_stream` retried, but
**within the same stale context**, so every retry kept failing — only a
freshly-spawned process (fresh context) could open the mic. Nothing in the
recorder rebuilt the context, so the app could not self-heal.

**Done (`recorder.py` `_open_stream`):**
- On any failed open, tear down and rebuild the PortAudio context
  (`sd._terminate()` + `sd._initialize()`) before retrying — this re-enumerates
  devices and clears the stale HAL state. Safe here: no stream is open at this
  point in the recorder.
- Retry count 2 → 3 (one extra attempt now that each retry is meaningful).

**Verified:** terminate+initialize cycle followed by a real `InputStream` open
succeeds; ruff clean; `pytest` → 128 passed. App relaunched once to load the fix
(`open -a Pysar`, live `src/`, no `make app` → TCC intact); mic opens clean.

**Note:** the already-stuck process from this morning could only be recovered by
that one relaunch — Python won't hot-reload the module and its PortAudio can't be
reset from outside. Going forward the new code self-heals a held/stale mic without
any restart.

**Can break:** `_terminate`/`_initialize` are python-sounddevice private API but
the standard way to reset PortAudio; both calls are exception-suppressed, so a
reinit failure can't crash the open path — worst case it behaves like the old
retry. Must not be called while a stream is open (it isn't, in `_open_stream`).
