.PHONY: all setup install venv whisper-build whisper-model whisper-vad whisper run up app icon lint fmt test clean distclean

# Full install in one go: everything + the menu-bar app. (install.sh wraps this
# with prerequisite + clone handling for a fresh Mac.)
all: setup app

# ── Config ──────────────────────────────────────────────────────────────────
# Override any of these via env; defaults install everything into vendor/.
WHISPER_DIR        ?= vendor/whisper.cpp
WHISPER_REPO       ?= https://github.com/ggerganov/whisper.cpp.git
WHISPER_SERVER     ?= $(WHISPER_DIR)/build/bin/whisper-server
WHISPER_MODEL_NAME ?= large-v3-turbo-q5_0
WHISPER_MODEL      ?= $(WHISPER_DIR)/models/ggml-$(WHISPER_MODEL_NAME).bin
WHISPER_VAD_NAME   ?= silero-v5.1.2
WHISPER_VAD_MODEL  ?= $(WHISPER_DIR)/models/ggml-$(WHISPER_VAD_NAME).bin
WHISPER_PORT       ?= 8080
WHISPER_LANG       ?= en

PY = . venv/bin/activate &&

# ── User-facing targets ─────────────────────────────────────────────────────

# One-shot install: venv + python deps + whisper.cpp + speech & VAD models.
setup: install whisper-build whisper-model whisper-vad
	@echo ""
	@echo "✅ Done. Next:"
	@echo "   make app   # install the menu-bar app into /Applications (recommended)"
	@echo "   make up    # or just run server + app from this terminal"

venv:
	@test -d venv || python3 -m venv venv

# Editable install with macOS- and dev-extras.
install: venv
	$(PY) pip install -q --upgrade pip
	$(PY) pip install -q -e '.[macos,dev]'

run:
	$(PY) python -m pysar

# One command: start whisper server (background) + app (foreground).
up:
	@bash scripts/start.sh

# Install the Dock-less menu-bar app into /Applications + `pysar` alias.
app:
	@bash scripts/install_app.sh

# Regenerate the app icon (assets/Pysar.icns) from scratch.
icon:
	$(PY) python scripts/make_icon.py assets/icon-1024.png
	@rm -rf assets/Pysar.iconset && mkdir -p assets/Pysar.iconset
	@for sz in 16 32 64 128 256 512; do \
		sips -z $$sz $$sz assets/icon-1024.png --out assets/Pysar.iconset/icon_$${sz}x$${sz}.png >/dev/null; \
		d=$$((sz*2)); sips -z $$d $$d assets/icon-1024.png --out assets/Pysar.iconset/icon_$${sz}x$${sz}@2x.png >/dev/null; \
	done
	@sips -z 1024 1024 assets/icon-1024.png --out assets/Pysar.iconset/icon_512x512@2x.png >/dev/null
	iconutil -c icns assets/Pysar.iconset -o assets/Pysar.icns
	@rm -rf assets/Pysar.iconset

whisper:
	@test -x "$(WHISPER_SERVER)" || (echo "❌ whisper-server not built. Run: make whisper-build" && exit 1)
	@test -f "$(WHISPER_MODEL)"  || (echo "❌ model not downloaded. Run: make whisper-model"  && exit 1)
	$(WHISPER_SERVER) --model $(WHISPER_MODEL) --host 127.0.0.1 --port $(WHISPER_PORT) --language $(WHISPER_LANG) --flash-attn --split-on-word --suppress-nst --vad --vad-model $(WHISPER_VAD_MODEL)

# ── Quality ─────────────────────────────────────────────────────────────────
lint:
	$(PY) ruff check .
	$(PY) ruff format --check .

fmt:
	$(PY) ruff format .
	$(PY) ruff check --fix .

test:
	$(PY) pytest

# ── whisper.cpp: clone + build ──────────────────────────────────────────────
whisper-build: $(WHISPER_SERVER)

$(WHISPER_SERVER):
	@if [ ! -d "$(WHISPER_DIR)/.git" ]; then \
		echo "📥 Cloning whisper.cpp into $(WHISPER_DIR)…"; \
		mkdir -p $(dir $(WHISPER_DIR)); \
		git clone --depth 1 $(WHISPER_REPO) $(WHISPER_DIR); \
	fi
	@echo "🔨 Building whisper.cpp (Metal is enabled automatically on Apple Silicon)…"
	cmake -B $(WHISPER_DIR)/build -S $(WHISPER_DIR) -DWHISPER_BUILD_SERVER=ON -DCMAKE_BUILD_TYPE=Release
	cmake --build $(WHISPER_DIR)/build --target whisper-server -j

# ── whisper.cpp: model ──────────────────────────────────────────────────────
whisper-model: $(WHISPER_MODEL)

$(WHISPER_MODEL):
	@if [ ! -d "$(WHISPER_DIR)" ]; then \
		echo "❌ Clone whisper.cpp first: make whisper-build"; exit 1; \
	fi
	@echo "📥 Downloading model $(WHISPER_MODEL_NAME) (~550 MB)…"
	cd $(WHISPER_DIR) && bash ./models/download-ggml-model.sh $(WHISPER_MODEL_NAME)

# ── whisper.cpp: VAD model (kills silence hallucinations) ────────────────────
whisper-vad: $(WHISPER_VAD_MODEL)

$(WHISPER_VAD_MODEL):
	@if [ ! -d "$(WHISPER_DIR)" ]; then \
		echo "❌ Clone whisper.cpp first: make whisper-build"; exit 1; \
	fi
	@echo "📥 Downloading VAD model $(WHISPER_VAD_NAME) (~1 MB)…"
	cd $(WHISPER_DIR) && bash ./models/download-vad-model.sh $(WHISPER_VAD_NAME)

# ── Cleanup ─────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist *.egg-info src/*.egg-info

# Wipe everything, including venv and whisper.cpp.
distclean: clean
	rm -rf venv $(WHISPER_DIR)
