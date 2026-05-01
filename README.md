# Cream Typer

[![CI](https://github.com/adjacentai/cream-typer/actions/workflows/ci.yml/badge.svg)](https://github.com/adjacentai/cream-typer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![macOS](https://img.shields.io/badge/macOS-Apple_Silicon-black?logo=apple)](https://www.apple.com/mac/)

### 🎙️ Voice translation in any direction. Locally on Apple Silicon.

**Tap Caps Lock, speak any language, get any other.** Whisper.cpp, no cloud, no GPU rental.

---

## 🌍 What it does

You speak in **any** language. Whatever language you picked in the menu bar — that's what comes out. Real examples:

| You said (any language goes)        | Active mode | What got pasted at the cursor       |
|-------------------------------------|-------------|-------------------------------------|
| 🇷🇺 «Привет, как у тебя дела?»        | 🇬🇧 `en`     | Hello, how are you doing?           |
| 🇬🇧 "Let's ship it on Friday"         | 🇷🇺 `ru`     | Давай выкатим в пятницу             |
| 🇩🇪 "Können wir morgen reden?"        | 🇯🇵 `ja`     | 明日話せますか？                     |
| 🇰🇷 "안녕하세요, 만나서 반갑습니다"          | 🇸🇦 `ar`     | مرحبًا، تشرفت بلقائك                |
| 🇯🇵 「コードレビューありがとう」           | 🇺🇦 `uk`     | Дякую за рев'ю коду                 |
| **anything**                         | 🌐 `→ English` | always English — flagship mode     |

**Why this works at all.** Whisper's encoder produces a language-agnostic representation of audio — meaning, not words. The decoder writes that meaning down in whichever language you asked for. Swap the `language` token, get a different output language. Same speech in, different writing out.

This is something the native `task=translate` flag **can't do** on `large-v3-turbo` — that model was fine-tuned without translation data and the flag is broken. We sidestep it.

**16 modes** in the menu bar: 15 target languages + the flagship `🌐 → English (from any)` shortcut. Click to switch, next dictation lands in the new language.

---

## ✨ Where this beats typing

- **Multilingual teams** — speak Russian to your dev chat, English to your PR description, German to your designer Slack — without changing keyboard layouts.
- **Coding while talking** — narrate the logic out loud, get clean prose in your PR, RFC, commit message, or Notion doc.
- **Faster than typing for non-English natives** — your brain composes in your native language, the text lands in whichever language the chat needs.
- **Voice notes during meetings** — instant text in Notes / Obsidian, no «record now, transcribe later» loop.
- **Translating quotes / tweets / headlines** — read out loud in any language, get any other language back.

---

## ⚡ Architecture

```
Caps Lock (tap)  →  🎙️ recording…
Caps Lock (tap)  →  whisper.cpp (localhost:8080)  →  clipboard  →  Cmd+V
```

Tap **Caps Lock** → speak → tap again → text appears at your cursor in **any app**: Slack, Notes, VS Code, your browser, terminal. The previous clipboard contents are saved and restored automatically.

| Stage | What happens |
|---|---|
| **Hotkey** | Caps Lock (keycode 57) via Quartz CGEventTap. Toggle: 1st tap starts, 2nd tap stops. Doesn't block input to other applications. |
| **Capture** | `sounddevice` at 16 kHz mono float32. WAV stays in memory (`io.BytesIO`) — never written to disk. |
| **STT** | `POST` to `localhost:8080/inference` (whisper.cpp). The `large-v3-turbo-q5_0` model runs on Metal GPU. |
| **Paste** | `pbcopy` + Cmd+V via CGEvent. The previous clipboard is saved and restored. |

**Latency:** ~0.3-0.5 s for 10 s of speech on Apple Silicon (Metal GPU). **Privacy:** zero network egress — audio never leaves your Mac. **Cost:** zero — model is downloaded once (~550 MB), inference is free forever.

**Platform:** macOS (Apple Silicon flagship; Intel Mac works without Metal). Windows / Linux backends — TBD.

---

## Install

Requires Python 3.10+, `cmake`, and `git`. Everything else (whisper.cpp + the model) is installed by one command:

```bash
cd cream_typer
make setup
```

What `make setup` does:
1. Creates `venv/` and installs the package in editable mode with macOS- and dev-extras (`pip install -e '.[macos,dev]'`).
2. Clones [whisper.cpp](https://github.com/ggerganov/whisper.cpp) into `vendor/whisper.cpp` and builds `whisper-server` via cmake (Metal is enabled automatically on Apple Silicon).
3. Downloads the `ggml-large-v3-turbo-q5_0.bin` model (~550 MB) into `vendor/whisper.cpp/models/`.

**Already have whisper.cpp installed elsewhere?** Override the paths via env (export them or pass them to make):

```bash
WHISPER_DIR=~/code/whisper.cpp make whisper
# or per-file:
WHISPER_SERVER=/path/to/whisper-server WHISPER_MODEL=/path/to/ggml.bin make whisper
```

Subtargets if something specific failed: `make install`, `make whisper-build`, `make whisper-model`. Full wipe — `make distclean`.

---

## Run

In two terminals:

```bash
# Terminal 1 — whisper server (run once, keep it up)
make whisper

# Terminal 2 — the app itself
make run
```

A 🎙 icon appears in the menu bar. Press Caps Lock, speak, press again — text is pasted wherever the cursor is.

---

## Development

```bash
make lint    # ruff check + format check
make fmt     # ruff format + ruff check --fix
make test    # pytest
```

CI runs the same commands on every PR (see [.github/workflows/ci.yml](.github/workflows/ci.yml)). Architecture notes and how to add your own backend live in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## macOS permissions

| Permission | Where to enable | Why |
|---|---|---|
| **Input Monitoring** | Settings → Privacy → Input Monitoring | CGEventTap (Caps Lock interception) |
| **Microphone** | Settings → Privacy → Microphone | Audio capture |
| **Accessibility** | Settings → Privacy → Accessibility | CGEventPost (Cmd+V simulation) |

Add **Terminal** (or iTerm) — not Python itself — since the app inherits permissions from its parent.

macOS will pop up the permission dialogs automatically the first time you run the app.

---

## Project layout

```
cream_typer/
├── src/                   # imported as `cream_typer` (see pyproject.toml)
│   ├── __init__.py        # __version__
│   ├── __main__.py        # `python -m cream_typer`
│   ├── app.py             # business logic, NO platform-specific code
│   ├── config.py          # constants and transcription modes
│   ├── recorder.py        # sounddevice → WAV in memory (io.BytesIO)
│   ├── transcriber.py     # HTTP client for the whisper.cpp server
│   └── backend/           # platform adapters (hotkey / paste / tray)
│       ├── __init__.py    # dispatch by sys.platform
│       ├── _base.py       # Protocol contracts for contributors
│       ├── _macos.py      # Quartz CGEventTap + Cmd+V + rumps  ✅
│       ├── _windows.py    # pynput + pystray                   🚧 TBD
│       └── _linux.py      # pynput + pystray (X11)             🚧 TBD
├── tests/                 # pytest smoke + transcriber mocks
├── scripts/
│   └── whisper_server.sh  # alternative to `make whisper`
├── .github/
│   ├── workflows/ci.yml   # lint + tests on macOS
│   ├── ISSUE_TEMPLATE/    # bug / feature
│   └── PULL_REQUEST_TEMPLATE.md
├── pyproject.toml         # build / deps / ruff / pytest config
├── Makefile               # setup / run / lint / fmt / test / whisper / distclean
├── CHANGELOG.md           # Keep a Changelog
├── CONTRIBUTING.md        # how to contribute
└── LICENSE                # MIT
```

---

## Available languages

Pick a mode by clicking inside the **🌍 Languages** submenu in the menu bar. The active one is checkmarked. The current set:

```
🌐 → English (from any)        ← flagship "translate anything to English" shortcut
🇬🇧 English        🇺🇦 Українська     🇪🇸 Español
🇩🇪 Deutsch        🇫🇷 Français       🇮🇹 Italiano
🇵🇹 Português      🇳🇱 Nederlands     🇵🇱 Polski
🇯🇵 日本語         🇨🇳 中文           🇰🇷 한국어
🇹🇷 Türkçe         🇹🇭 ไทย            🇻🇳 Tiếng Việt
🇸🇦 العربية         🇷🇺 Русский
```

**Want a different set?** Edit `MODES`, `MODE_LABELS`, and `MENU_MODES` in [src/config.py](src/config.py). Whisper supports 99 languages — adding any of them is a single line in three dicts, no UI code required.

**Note on Thai (th):** the `large-v3-turbo` model has noticeably degraded performance on Thai compared to `large-v3` (Whisper's original quality matrix). It still works, but expect more errors. Vietnamese is fine.

---

## Configuration

Everything lives in [src/config.py](src/config.py):

```python
HOTKEY_KEYCODE = 57              # Caps Lock. 60=Right Shift, 61=Right Option, 54=Right Cmd
SAMPLE_RATE    = 16000           # whisper.cpp expects 16 kHz
WHISPER_URL    = "http://localhost:8080/inference"
DEFAULT_MODE   = "en"            # "en" / "ru" / "translate" / "uk" / ...
MIN_RECORDING_SEC = 0.3          # shorter taps are ignored
CLIPBOARD_RESTORE_DELAY = 0.15   # delay before the previous clipboard is restored
```

The whisper-server's default language lives in the [Makefile](Makefile) under `WHISPER_LANG`, but it's only a fallback: the client always passes `language` explicitly from `MODES`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Hotkey doesn't fire | No Input Monitoring permission | Settings → Privacy → Input Monitoring → Terminal |
| Text isn't pasted | No Accessibility permission | Settings → Privacy → Accessibility → Terminal |
| `⚠️ Whisper not running` in the menu | Server isn't up | `make whisper` in a separate terminal |
| Empty transcription / `⚠️ Silence` | Too quiet, or shorter than `MIN_RECORDING_SEC` | Speak louder / hold the tap longer than 0.3s |
| `⚠️ Too short` | Caps Lock tap shorter than `MIN_RECORDING_SEC` | Hold longer — this guards against accidental taps |
| Double-fire on Shift+Caps | macOS clears AlphaShift on shift+caps | Already handled in [src/backend/_macos.py](src/backend/_macos.py) — events with Shift are ignored |
| Wrong output language | Wrong mode active | Menu bar → 🌍 Languages → pick the right one |

---

## How this differs from other OSS apps

The space is crowded; here's what I'd pick depending on what you need:

| Project | Stack | Hotkey | Backend | Notable |
|---|---|---|---|---|
| **cream_typer** *(this)* | Python + rumps | **Caps Lock toggle** | whisper.cpp HTTP | On-the-fly translation by swapping `language` instead of `translate=true` |
| [foges/whisper-dictation](https://github.com/foges/whisper-dictation) | Python + rumps | Cmd+Option (toggle) | openai-whisper (PyTorch) | The well-known reference — but it loads the model into RAM every time |
| [pindrop](https://github.com/watzon/pindrop) | Swift native | hold-to-talk | WhisperKit (Core ML) | Fully native, the best perf/battery story |
| [vocamac](https://github.com/jatinkrmalik/vocamac) | Swift | hold-to-talk | WhisperKit | Tiny model bundled in the box, works out of the box |
| [open-wispr](https://github.com/human37/open-wispr) | Electron | hold Globe (🌐) | whisper.cpp | Friendly onboarding, but Electron |
| [openwhispr](https://github.com/OpenWhispr/openwhispr) | Electron, cross-platform | hold | local + cloud (BYOK) | Mac/Windows/Linux in one binary |
| [GoWhisper](https://github.com/stephanwesten/GoWhisper) | Go | hold | whisper.cpp | Built around terminal / Claude Code |
| [AudioWhisper](https://github.com/mazdak/AudioWhisper) | Swift | hold | OpenAI API / Gemini | Not local — sends audio to the cloud |

**Pick cream_typer when:**
- You want a toggle (tap-talk-tap) instead of holding a key.
- You already have a whisper.cpp build and don't want yet another process bundled.
- You want dictation in any language with auto-translation to English (and back).
- You care about codebase size — this is ~300 lines, the whole thing reads in 10 minutes.

**Pick something else when:**
- You want a native macOS app without Python and cmake → [pindrop](https://github.com/watzon/pindrop) or [vocamac](https://github.com/jatinkrmalik/vocamac).
- You need Windows/Linux **today** → [openwhispr](https://github.com/OpenWhispr/openwhispr).
- You'd rather not deal with config → [open-wispr](https://github.com/human37/open-wispr) (better onboarding).

---

## Acknowledgments

This project stands on the shoulders of others:

- **[ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp)** — the C++ inference engine that makes local Whisper fast enough on consumer hardware. The entire reason this project is possible.
- **[OpenAI Whisper](https://github.com/openai/whisper)** — the speech-recognition model architecture and the language-agnostic encoder we exploit for translation.
- **[rumps](https://github.com/jaredks/rumps)** — Pythonic macOS menu-bar bindings.
- The dictation OSS scene — [foges/whisper-dictation](https://github.com/foges/whisper-dictation), [pindrop](https://github.com/watzon/pindrop), [open-wispr](https://github.com/human37/open-wispr), [vocamac](https://github.com/jatinkrmalik/vocamac) — for showing the path.

---

## ⭐ Star history

[![Star History Chart](https://api.star-history.com/svg?repos=adjacentai/cream-typer&type=Date)](https://star-history.com/#adjacentai/cream-typer&Date)

---

## Made by

Built by [**NeCL**](https://neclco.com) — AI engineering studio shipping local-first AI: production RAG, real-time voice agents, on-prem deployment.

Need something custom? [neclco.com](https://neclco.com) · [Telegram @ownerai](https://t.me/ownerai) · neclcompany@gmail.com
