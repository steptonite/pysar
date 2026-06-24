# Contributing to Pysar

Thanks for stopping by. PRs are welcome — from typo fixes to new platform backends.

## Quick start

```bash
git clone https://github.com/adjacentai/cream-typer.git
cd cream-typer
make setup            # venv + python deps + whisper.cpp + model
make whisper          # in a separate terminal
make run              # in yet another terminal
```

## Development

```bash
make lint     # ruff check
make fmt      # ruff format (in-place)
make test     # pytest
```

CI runs all of these on every PR. To catch everything locally in one go — `make lint test`.

## Architecture in two sentences

- **`src/app.py`** — business logic. Knows nothing about macOS / Windows.
- **`src/backend/`** — platform adapters. Each backend implements the Protocol classes from `_base.py` (`HotkeyBackend`, `PasteBackend`, `TrayBackend`).
- **`src/config.py`** — every constant (hotkey, sample rate, language modes).

## Adding a new backend (Windows / Linux / etc.)

1. Create `src/backend/_<platform>.py` with the three classes `HotkeyListener`, `Paster`, and `Tray`. The contract is in [src/backend/_base.py](src/backend/_base.py).
2. Add a branch in [src/backend/__init__.py](src/backend/__init__.py).
3. Add the optional dependencies to `pyproject.toml` (`[project.optional-dependencies]`).
4. Document the setup in `docs/INSTALL_<PLATFORM>.md` (create one if it doesn't exist).
5. Extend the CI matrix in `.github/workflows/ci.yml`.

## Adding a new language to the submenu

It's a one-to-one edit in three dicts inside [src/config.py](src/config.py): `MODES`, `MODE_LABELS`, `MENU_MODES`. No UI code changes needed.

## Code style

- Linter: `ruff` (config in `pyproject.toml`).
- Type hints are welcome but not required on every line.
- Comments — only when the «why» is non-obvious, not the «what». Good example: the Caps Lock + Shift comment in `_macos.py`.
- Identifiers in code — English. UI strings — English (we ship internationally).

## Issues

- **Bug** — use the template, include the macOS version, repro steps, and the relevant `Console.app` slice if any.
- **Feature** — open an issue first to align on scope, then a PR. We don't want you spending a day on code we'd reject for architectural reasons.

## License

Your contributions are accepted under the [MIT](LICENSE) license — the same one the project uses.
