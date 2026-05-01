.PHONY: setup install venv whisper-build whisper-model whisper run lint fmt test clean distclean

# ── Config ──────────────────────────────────────────────────────────────────
# Override any of these via env; defaults install everything into vendor/.
WHISPER_DIR        ?= vendor/whisper.cpp
WHISPER_REPO       ?= https://github.com/ggerganov/whisper.cpp.git
WHISPER_SERVER     ?= $(WHISPER_DIR)/build/bin/whisper-server
WHISPER_MODEL_NAME ?= large-v3-turbo-q5_0
WHISPER_MODEL      ?= $(WHISPER_DIR)/models/ggml-$(WHISPER_MODEL_NAME).bin
WHISPER_PORT       ?= 8080
WHISPER_LANG       ?= en

PY = . venv/bin/activate &&

# ── User-facing targets ─────────────────────────────────────────────────────

# One-shot install: venv + python deps + whisper.cpp + model.
setup: install whisper-build whisper-model
	@echo ""
	@echo "✅ Done. Next:"
	@echo "   terminal 1:  make whisper"
	@echo "   terminal 2:  make run"

venv:
	@test -d venv || python3 -m venv venv

# Editable install with macOS- and dev-extras.
install: venv
	$(PY) pip install -q --upgrade pip
	$(PY) pip install -q -e '.[macos,dev]'

run:
	$(PY) python -m pysar

whisper:
	@test -x "$(WHISPER_SERVER)" || (echo "❌ whisper-server not built. Run: make whisper-build" && exit 1)
	@test -f "$(WHISPER_MODEL)"  || (echo "❌ model not downloaded. Run: make whisper-model"  && exit 1)
	$(WHISPER_SERVER) --model $(WHISPER_MODEL) --host 127.0.0.1 --port $(WHISPER_PORT) --language $(WHISPER_LANG)

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

# ── Cleanup ─────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist *.egg-info src/*.egg-info

# Wipe everything, including venv and whisper.cpp.
distclean: clean
	rm -rf venv $(WHISPER_DIR)
