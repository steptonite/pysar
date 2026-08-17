# Pysar

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-Apple_Silicon-black?logo=apple)](https://www.apple.com/mac/)

**Pysar** (Ukrainian for *scribe*) is an offline voice workstation that lives in your macOS menu bar.

Tap **Caps Lock**, speak, tap again — your words are transcribed locally by whisper.cpp on the Metal GPU and land at your cursor in whatever app you're in. Beyond dictation it transcribes meetings, media files and system audio, all on the same local engine.

**Nothing leaves the Mac.** No cloud, no account, no telemetry, no per-minute billing. The model downloads once; every transcription after that is free.

Built and tuned on an **Apple M2 with 8 GB** of unified memory — the constraint that shaped most of the design decisions below.

---

## What it does

**Dictation**

- **Two dictation modes.** *Batch* transcribes the whole take at once and pastes it — the cleanest result for a long, considered thought. *Streaming* types each sentence into the field while you keep talking, cutting audio on natural pauses (never mid-word) and transcribing sentence-by-sentence through a single serialized worker, so word order holds and whisper is never run concurrently. Choose in **Settings → Dictation**. → [Streaming dictation](#streaming-dictation)
- **Focus-aware safety.** Streaming watches where the keyboard focus actually is. Switch to another text field and it follows you there. Switch somewhere with no field at all — Spotlight, the desktop — and it refuses to type blind: the rest of the take is collected and handed to your clipboard in one piece when you stop, so nothing lands in the wrong window.
- **Status pill.** A small floating overlay near the menu-bar icon shows the live state — listening, recognizing, buffering — so you can tell it's working even when your dictation key is a silent one with no LED.
- **19 output languages.** `Ctrl+Option+U` (🇺🇦) · `R` (🇷🇺) · `E` (🌐 → English from *any* spoken language), with 17 more in the **🌍 Languages** submenu. The menu-bar flag shows what's active. Every shortcut — dictation toggle, languages, profile sets — is reassignable live in **Settings → Hotkeys**, no relaunch.

**Accuracy**

- **Speech profiles.** Per-language priming sentences that bias Whisper toward *your* vocabulary — tool names, jargon, slang, proper nouns — so it stops mangling them. The composed prompt is capped to a token budget with a live meter. A built-in **Copy AI prompt** hands any chat model a meta-prompt that returns importable profile JSON; pasting it back tolerates smart quotes and trailing commas. → [docs/speech-profiles.md](docs/speech-profiles.md)
- **Profile sets.** Bundle several profiles into a named set and arm the whole set with one key (`Ctrl+Option+<digit>`). Settings shows which set is live and drops that mark the moment you hand-edit a toggle.
- **VAD instead of word filters.** The server runs Silero VAD, so silence never reaches the model and Whisper stops inventing subtitle-credit junk on quiet input. Because the hallucination problem is solved upstream of the model, Pysar does **no content-based filtering** — real words are never silently dropped. Paired with `--split-on-word` and `--suppress-nst`.

**Beyond dictation**

- **Transcribe everything (meeting mode).** An independent capture: system audio *and* your mic, transcribed live into a floating **liquid-glass island** (a real macOS 26 `NSGlassEffectView`) that stays above everything, including other apps' fullscreen video. Draggable, resizable, adjustable transparency, optionally saved as a timestamped Markdown transcript. Speaker separation runs off, fast (loudness), or smart (both streams decoded separately). → [Transcribe everything](#transcribe-everything)
- **File transcription.** Point Pysar at an audio or video file and get Markdown back: ffmpeg decodes it, the audio is chunked and run through the same local whisper instance. Requires `ffmpeg`; live dictation does not.
- **Text enhancement.** An optional post-dictation pass that rewrites the transcript through a small **local** LLM before it's pasted — like a "rephrase" button, offline and in your own voice. → [Text enhancement](#text-enhancement-llm-styling-via-ollama)
- **Recording archive.** Off by default — audio stays in memory. Turn on **💾 Save recordings** to keep the last 5/10/20 WAVs on disk (auto-pruned), so a failed or aborted take can be re-transcribed instead of re-spoken.

**The app itself**

- **A real menu-bar agent.** `make app` installs `/Applications/Pysar.app` — an `LSUIElement` agent with its own icon, launchable from Spotlight or Launchpad. No Dock tile, no Terminal window, no Python rocket bouncing at login. The bundle's compiled launcher is the responsible process, which is what gives macOS one stable identity to attach permissions to.
- **One command to run.** Launching the app starts the whisper server in the background; quitting stops it. `make up` does the same from a terminal.
- **Drill-in Settings.** A native WebKit panel rather than an ever-taller menu: a main page (audio, recordings, theme, UI language) with drill-ins for Speech profiles, Hotkeys and Transcribe everything. Auto / Light / Dark with a live accent.
- **Bilingual interface (🇺🇦/🌐).** Menu bar, status line, notifications and Settings switch between Ukrainian and English live — independent of the language you're dictating in.
- **Reproducible.** `make setup && make app` rebuilds the whole thing from scratch, including a clean macOS reinstall. Covered by 21 test modules over ~11 k lines.

Model: `large-v3-turbo-q5_0` — the best speed/quality fit for 8 GB of unified memory. Capture runs at 48 kHz and is decimated to whisper's 16 kHz through an anti-aliasing FIR filter (numpy only, no scipy in the dependency tree).

---

## Install

**One command** on an Apple Silicon Mac. It installs what's missing (Homebrew, `cmake`, `git`, Python, `ffmpeg`), clones the repo, builds whisper.cpp, downloads the models and installs the app:

```bash
curl -fsSL https://raw.githubusercontent.com/steptonite/pysar/main/install.sh | bash
```

Already cloned? Run the same script from inside the repo, or drive the Makefile directly:

```bash
./install.sh        # bootstrap (deps + setup + app), idempotent
```

```bash
make all            # setup + app, if cmake/git/python/ffmpeg are already present
```

`make setup` (venv + whisper.cpp + ~550 MB speech model + Silero VAD) and `make app` (build `Pysar.app` + the `pysar` alias) can be run separately.

### Updating

There is no update checker — the app never talks to the network on its own. To
take the latest version, run the same one-liner again:

```bash
curl -fsSL https://raw.githubusercontent.com/steptonite/pysar/main/install.sh | bash
```

It is idempotent: it fast-forwards the clone, rebuilds only what changed and
reinstalls the app. Local edits in the clone are parked in `git stash` (never
discarded) so the update can land — `git stash pop` brings them back. Relaunch
Pysar afterwards; if the rebuild changed the app's signature, macOS will ask for
Input Monitoring and Accessibility again.

Then launch **Pysar** from Spotlight and grant **Input Monitoring** and **Accessibility** in System Settings → Privacy & Security. macOS prompts for Microphone on its own; it never prompts for the other two — see [macOS permissions](#macos-permissions).

---

## Use

| Action | How |
|---|---|
| Dictate | Caps Lock → speak → Caps Lock |
| Switch language | `Ctrl+Option+U` 🇺🇦 · `R` 🇷🇺 · `E` 🌐→EN, or the 🌍 submenu |
| Arm a profile set | `Ctrl+Option+<digit>` |
| Meeting mode | **🎧 Transcribe everything** in the menu bar |
| Settings | menu bar → Settings |
| Quit | menu bar → Quit (stops the whisper server too) |

To change the language line-up, edit `MODES`, `MODE_LABELS`, `MENU_MODES` and the hotkeys in [src/config.py](src/config.py).

---

## Streaming dictation

**Batch** (default) records, stops, transcribes the whole clip and pastes once. **Streaming** types each sentence into the field while you keep talking.

- **Pause-based cutting.** A segment ends on a natural pause once enough has been said — never mid-word. A run-on with no pause at all is force-cut at a hard cap (~18 s) as a rare fallback. Tunable in [src/config.py](src/config.py): `PAUSE_SEC`, `MIN_SEG_SEC`, `MAX_SEG_SEC`, `SILENCE_MARGIN`.
- **In order, never concurrent.** Segments queue to a single worker that transcribes and types them one at a time — word order holds, and whisper is never run in parallel (which would thrash 8 GB).
- **No clipboard.** Sentences are typed as synthetic Unicode key events, so whatever you had copied is still there when you finish.
- **Focus-aware.** Checked before every sentence; falls back to buffer-and-clipboard rather than typing into nowhere.

**Trade-off:** streaming reaches first text much sooner but is slightly less accurate on the same audio — short segments give Whisper less context to work with. Stay on batch when you want the cleanest possible transcript of a long take.

---

## Transcribe everything

An independent capture mode for meetings, calls and streams: both **system audio and your mic** are transcribed live, side by side.

- **Floating island.** A borderless **Liquid Glass** panel (macOS 26 `NSGlassEffectView`, with an `NSVisualEffectView` fallback on older macOS) shows the transcript as it's recognized, each line stamped `Source · HH:MM`. It floats above everything — including fullscreen video — is freely draggable and resizable down to a compact strip, and remembers its geometry across launches.
- **Stop from the island.** The panel carries its own **Stop** button, so a capture can always be ended without the menu-bar item — which a crowded menu bar can hide behind the notch. It says *Stopping…* while the queue drains (the last sentences are still being transcribed), so the click is never mistaken for a dead button.
- **Adjustable glassiness.** From a solid themed panel to near-full glass. The tint underlay sits on a separate layer *below* the text, so turning transparency up never fades the words themselves.
- **Speaker separation.** Off (one mixed stream) · Fast (dominant source by loudness) · Smart (system and mic decoded separately through the same whisper.cpp instance — more accurate, a little slower).
- **Context hint.** A custom priming sentence for the session, with the same token-budget meter as Speech profiles, or inherit the active dictation profiles for that language.
- **Saved to disk (optional).** Writes a timestamped Markdown file per session to `~/Library/Application Support/Pysar/transcripts/`. **Record without the window** keeps the file but never shows the island, for a fully out-of-sight capture.

Design log: [docs/meeting-mode-settings.md](docs/meeting-mode-settings.md).

---

## Text enhancement (LLM styling via Ollama)

An optional pass that rewrites the transcript through a small **local** model before pasting. Enable in **Settings → Text enhancement**.

- **Requires [Ollama](https://ollama.com)** on `127.0.0.1:11434`. Pysar never starts or stops it — if it's down, dictation simply pastes the raw text.
- **Recommended model:** `hf.co/INSAIT-Institute/MamayLM-Gemma-3-4B-IT-v1.0-GGUF:Q4_K_M` (~2.5 GB), which won a blind bench on real Ukrainian and surzhyk dictations (02.07.2026; runner-up `gemma3:4b`). Any Ollama model can be selected; on 8 GB machines stay at ≤4B q4.
- **Styles.** Presets (Business, Concise, Casual, Bullet points, No profanity) or **My style**, composed from the *style* field of your active Speech profiles so the rewrite keeps your voice rather than flattening it.
- **Never blocks dictation.** Any error or timeout falls back to the raw transcript, and the model is kept warm only briefly (`keep_alive 5m`) so it doesn't pin your RAM.

---

## How it works

```
Caps Lock  →  🎙 capture 48 kHz  →  FIR decimation → 16 kHz
           →  whisper.cpp (127.0.0.1:8080, Metal + Silero VAD)
           →  batch:     clipboard → ⌘V → clipboard restored
              streaming: synthetic Unicode key events (clipboard untouched)
```

**Speak any language, get any other.** Whisper's encoder produces a language-agnostic representation of the audio — meaning, not words — and the decoder writes it down in whichever language the `language` token names. Swapping that token translates, without the broken `task=translate` flag that `large-v3-turbo` was fine-tuned without.

**Latency** ≈0.3–0.5 s per 10 s of speech on Apple Silicon, slower under memory pressure on 8 GB. **Network egress: zero.**

---

## macOS permissions

| Permission | Why | Prompted? |
|---|---|---|
| **Input Monitoring** | intercepting the dictation key (`CGEventTap`) | ❌ never — add manually |
| **Accessibility** | ⌘V paste (batch) and synthetic typing (streaming), both via `CGEventPost` | ❌ never — add manually |
| **Microphone** | audio capture | ✅ automatic |
| **Screen Recording** | ScreenCaptureKit system-audio capture — only for Transcribe everything | ✅ automatic |

Grant these to **Pysar**, not to Python or Terminal. macOS attributes permissions per responsible process, which is exactly why the app ships a compiled launcher instead of a shell script.

⚠️ The hotkey gate is the verdict of `IOHIDCheckAccess`, not whether the event tap was created. With Accessibility granted but Input Monitoring missing, the tap comes up perfectly and then silently receives nothing. Pysar polls the real verdict every 2 s and attaches hotkeys the moment you flip the switch — no relaunch needed.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Dictation key does nothing | No Input Monitoring | grant it to Pysar; it attaches within ~2 s |
| Text shown in the menu bar but never pasted | No Accessibility | grant it to Pysar |
| Permissions dialog never appears | macOS never prompts for these two | add Pysar manually in System Settings |
| Permissions look granted but nothing works | stale TCC row after a re-signed build | `tccutil reset All com.steptonite.pysar`, then re-grant |
| `⚠️ Whisper not running` | server isn't up | relaunch the app; it starts the server |
| Subtitle-credit junk on silence | Whisper silence hallucination | VAD handles it — confirm the server restarted |
| Wrong output language | wrong mode armed | `Ctrl+Option+U/R/E`; check the menu-bar flag |
| 10–20 s waits with Resolve or Photoshop open | 8 GB under memory pressure (swap) | dictate in shorter takes, or close the heavy app |
| Install ends after the dependencies, Pysar never builds | a child process ate the piped script | fixed 18.08.2026 — re-run the one-liner; it now buffers itself before running |
| Install fails inside pip / venv | the venv was built on macOS's Python 3.9 | fixed 18.08.2026 — `rm -rf venv` and re-run; the installer now requires ≥3.10 |
| App launches and vanishes, no window | the Metal backend crashed at model load (seen on an M4) | it now retries on the CPU by itself; the choice is kept in `~/Library/Application Support/Pysar/no-gpu` — delete that file to try the GPU again |

---

## Make targets

```
make setup        # full install: venv + whisper.cpp + speech & VAD models
make app          # install /Applications/Pysar.app + the `pysar` alias
make up           # run server (bg) + app from this terminal
make icon         # regenerate the app icon
make whisper-vad  # (re)download the Silero VAD model
make lint / fmt / test
make distclean    # wipe venv + vendored whisper.cpp
```

Config lives in [src/config.py](src/config.py); server flags in [scripts/whisper_server.sh](scripts/whisper_server.sh) and the [Makefile](Makefile).

---

## Attribution & License

Pysar runs on [whisper.cpp](https://github.com/ggerganov/whisper.cpp) by ggerganov, [OpenAI Whisper](https://github.com/openai/whisper), [Silero VAD](https://github.com/snakers4/silero-vad) and [rumps](https://github.com/jaredks/rumps). Thanks to all four.

Licensed under MIT.
