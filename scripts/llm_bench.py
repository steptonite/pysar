"""Blind benchmark of Ollama models for Pysar's Enhance feature.

Compares small local models on the style-transform task using the user's REAL
dictations, and writes a BLIND judgment sheet: the human judge scores variants
labeled А/Б/В/… without knowing which model produced which — the mapping lives
in a separate key file opened only after judging (LLM-as-judge is deliberately
not used; it inverts the owner's taste).

RAM discipline (8 GB): model-major loop — each model is loaded ONCE, runs all
inputs × styles, then `ollama stop` frees it before the next model.

Usage:
    venv/bin/python scripts/llm_bench.py --wavs ~/…/recordings/*.wav
    venv/bin/python scripts/llm_bench.py --texts a.txt b.txt --models gemma3:4b
"""

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

from pysar import server
from pysar.postprocessor import enhance, is_ollama_alive, preload
from pysar.profiles import DEFAULT_PROFILES, compose_style_prompt, style_example
from pysar.transcriber import transcribe

DEFAULT_MODELS = [
    "qwen3:4b-instruct-2507-q4_K_M",
    "gemma3:4b",
    "hf.co/INSAIT-Institute/MamayLM-Gemma-3-4B-IT-v1.0-GGUF:Q4_K_M",
    "gemma3:1b",
]
DEFAULT_STYLES = ["custom", "business", "emoji"]
MAX_INPUT_CHARS = 1500


def _truncate(text: str, limit: int = MAX_INPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[… текст обрізано для бенчмарку …]"


def _collect_inputs(wav_paths: list[Path], text_paths: list[Path], lang: str) -> list[dict]:
    """Inputs from real dictation WAVs (via the whisper server) and/or text files.
    Empty and duplicate texts are skipped so a re-run doesn't inflate the sheet."""
    inputs: list[dict] = []
    seen: set[str] = set()

    for wav in wav_paths:
        try:
            raw = wav.read_bytes()
        except OSError as e:
            print(f"⚠️ не читається {wav}: {e}", file=sys.stderr)
            continue
        text, err = transcribe(raw, mode=lang)
        if err or not text:
            print(f"⚠️ транскрипція {wav.name} не вдалась: {err or 'порожньо'}", file=sys.stderr)
            continue
        text = text.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        inputs.append({"id": f"in{len(inputs) + 1}", "source": wav.name, "text": _truncate(text)})
        print(f"📝 {wav.name}: {text[:80]}{'…' if len(text) > 80 else ''}")

    for txt in text_paths:
        try:
            text = txt.read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"⚠️ не читається {txt}: {e}", file=sys.stderr)
            continue
        if not text or text in seen:
            continue
        seen.add(text)
        inputs.append({"id": f"in{len(inputs) + 1}", "source": txt.name, "text": _truncate(text)})
        print(f"📝 {txt.name}: {text[:80]}{'…' if len(text) > 80 else ''}")

    return inputs


def _style_prompt(style_key: str, lang: str) -> str:
    """One variable at a time: 'custom' = the surzhyk profile's style_prompt only,
    a preset key = that preset only — so the judge compares models, not prompt mixes."""
    if style_key == "custom":
        return compose_style_prompt(DEFAULT_PROFILES, ["Суржик / розмова"], lang, None)
    return compose_style_prompt([], [], lang, style_key)


_LETTERS = "АБВГДЕЖИКЛ"


def _therm_state() -> int | None:
    """Return Apple Silicon thermal state (0–3) or None if unavailable."""
    try:
        from Foundation import NSProcessInfo  # type: ignore[import-untyped]

        return int(NSProcessInfo.processInfo().thermalState())
    except ImportError:
        return None


def _cooldown_gate(cooldown_fallback: int) -> None:
    """Wait until thermalState < 2 (or fallback timeout), then proceed."""
    therm = _therm_state()
    if therm is None:
        print(f"🌡 термодатчик недоступний — пауза {cooldown_fallback} с")
        time.sleep(cooldown_fallback)
        return

    waited = 0
    while therm is not None and therm >= 2 and waited < 300:
        print(f"🌡 thermalState={therm}, чекаю охолодження…")
        time.sleep(15)
        waited += 15
        therm = _therm_state()
    if waited >= 300:
        print("⚠️ 300 с очікування вичерпано — продовжую попри нагрів")


def _suspect_preamble(text: str) -> bool:
    """Check if the first line looks like a preamble (short, ends with ':' or starts with certain words)."""
    if not text:
        return False
    lines = text.splitlines()
    first = lines[0].rstrip()
    if len(first) > 80:
        return False
    rest = "\n".join(lines[1:]).strip()
    if not rest:
        return False
    if first.endswith(":"):
        return True
    preamble_words = (
        "Ось",
        "Зроблено",
        "Готово",
        "Виправлен",
        "Переписан",
        "Відредагован",
        "Звичайно",
    )
    return first.startswith(preamble_words)


def _build_groups(inputs: list[dict], results: list[dict]) -> list[dict]:
    """Group results by input×style, shuffle variants, assign letters.
    Returns list of groups for markdown/key/HTML generation."""
    groups: list[dict] = []
    for inp in inputs:
        for style in sorted({r["style"] for r in results if r["input_id"] == inp["id"]}):
            group_entries = [
                r for r in results if r["input_id"] == inp["id"] and r["style"] == style
            ]
            random.shuffle(group_entries)
            variants = []
            for i, r in enumerate(group_entries):
                letter = _LETTERS[i]
                variants.append(
                    {
                        "letter": letter,
                        "text": r["output_text"] or "",
                        "error": r["error"],
                        "model": r["model"],  # only for key, never shown in HTML
                    }
                )
            groups.append(
                {
                    "input_id": inp["id"],
                    "input_text": inp["text"],
                    "style": style,
                    "variants": variants,
                }
            )
    return groups


def _write_vote_html(
    out_dir: Path, inputs: list[dict], groups: list[dict], date_label: str
) -> None:
    """Write self-contained bench_vote.html for blind voting."""
    # Build a storage key from the date (spaces -> underscores)
    storage_key = f"pysar_bench_vote_{date_label.replace(' ', '_')}"

    # Prepare data to embed: we need input_id, style, original text, and variants (letter, text, error).
    # We'll embed the groups as JSON; no model names.
    groups_data = []
    for g in groups:
        variants_data = []
        for v in g["variants"]:
            variants_data.append(
                {
                    "letter": v["letter"],
                    "text": v["text"],
                    "error": v["error"],
                }
            )
        groups_data.append(
            {
                "input_id": g["input_id"],
                "style": g["style"],
                "input_text": g["input_text"],
                "variants": variants_data,
            }
        )

    html = f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<title>Pysar Bench — сліпе голосування</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
  --bg: #f5f5f5;
  --card-bg: #ffffff;
  --text: #222;
  --border: #ccc;
  --accent: #4a90d9;
  --accent-light: #e8f0fe;
  --muted: #888;
  --error-bg: #f7f7f7;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #1e1e1e;
    --card-bg: #2a2a2a;
    --text: #ddd;
    --border: #444;
    --accent: #5a9fd6;
    --accent-light: #1c3040;
    --muted: #999;
    --error-bg: #333;
  }}
}}
body {{
  font-family: system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  margin: 0;
  padding: 0 1em 2em;
}}
header {{
  position: sticky;
  top: 0;
  background: var(--bg);
  padding: 1em 0;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5em;
  z-index: 10;
}}
h1 {{ font-size: 1.4em; margin: 0; }}
.progress {{ font-weight: bold; }}
.group {{
  margin: 2em 0;
  padding: 1em;
  background: var(--card-bg);
  border-radius: 8px;
  border: 1px solid var(--border);
}}
.group h2 {{ margin-top: 0; font-size: 1.1em; }}
details {{
  margin-bottom: 1em;
  color: var(--muted);
}}
details pre {{
  white-space: pre-wrap;
  word-wrap: break-word;
}}
.cards {{
  display: flex;
  flex-wrap: wrap;
  gap: 1em;
}}
.card {{
  flex: 1 1 250px;
  border: 2px solid var(--border);
  border-radius: 8px;
  padding: 0.8em;
  background: var(--card-bg);
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
  display: flex;
  flex-direction: column;
}}
.card.error {{
  background: var(--error-bg);
  border-color: var(--border);
  cursor: default;
  opacity: 0.7;
}}
.card.selected {{
  border-color: var(--accent);
  background: var(--accent-light);
}}
.card .letter {{
  font-weight: bold;
  font-size: 1.2em;
  margin-bottom: 0.3em;
}}
.card pre {{
  white-space: pre-wrap;
  word-wrap: break-word;
  font-size: 0.9em;
  margin: 0.5em 0;
  flex: 1;
}}
.card textarea {{
  width: 100%;
  box-sizing: border-box;
  margin-top: 0.5em;
  font-family: inherit;
  font-size: 0.85em;
  padding: 0.3em;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg);
  color: var(--text);
  resize: vertical;
}}
.actions {{
  margin: 2em 0;
  display: flex;
  gap: 1em;
  flex-wrap: wrap;
}}
button {{
  padding: 0.5em 1.2em;
  font-size: 1em;
  border: 1px solid var(--accent);
  background: var(--accent);
  color: white;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
}}
button:hover {{ opacity: 0.9; }}
#fallback-pre {{
  display: none;
  background: var(--card-bg);
  border: 1px solid var(--border);
  padding: 1em;
  white-space: pre-wrap;
  word-wrap: break-word;
  max-height: 300px;
  overflow: auto;
  margin-top: 1em;
}}
</style>
</head>
<body>
<header>
  <h1>Pysar Bench — сліпе голосування</h1>
  <div class="progress" id="progress">Оцінено 0 з {len(groups)}</div>
  <button onclick="copyVerdict()">📋 Скопіювати вердикт</button>
</header>

<div id="groups-container"></div>

<div class="actions">
  <button onclick="copyVerdict()">📋 Скопіювати вердикт</button>
</div>
<pre id="fallback-pre"></pre>

<script>
const STORAGE_KEY = "{storage_key}";
const GROUPS = {json.dumps(groups_data, ensure_ascii=False)};

// State: {{ groupIdx: {{ winner: letter or null, notes: {{ letter: text }} }} }}
let state = {{}};

function loadState() {{
  try {{
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) state = JSON.parse(saved);
  }} catch (e) {{}}
  // Ensure all groups exist
  GROUPS.forEach((g, idx) => {{
    if (!state[idx]) state[idx] = {{ winner: null, notes: {{}} }};
  }});
}}

function saveState() {{
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}}

function updateProgress() {{
  const total = GROUPS.length;
  const voted = Object.values(state).filter(s => s.winner !== null).length;
  document.getElementById('progress').textContent = `Оцінено ${{voted}} з ${{total}}`;
}}

function render() {{
  const container = document.getElementById('groups-container');
  container.innerHTML = '';
  GROUPS.forEach((group, gIdx) => {{
    const gState = state[gIdx];
    const div = document.createElement('div');
    div.className = 'group';
    div.innerHTML = `
      <h2>${{group.input_id}} · ${{group.style}}</h2>
      <details>
        <summary>Оригінал диктовки</summary>
        <pre>${{escapeHtml(group.input_text)}}</pre>
      </details>
      <div class="cards" id="cards-${{gIdx}}"></div>
    `;
    container.appendChild(div);

    const cardsDiv = div.querySelector('.cards');
    group.variants.forEach((v, vIdx) => {{
      const card = document.createElement('div');
      card.className = 'card' + (v.error ? ' error' : '');
      if (gState.winner === v.letter && !v.error) card.classList.add('selected');
      card.dataset.groupIdx = gIdx;
      card.dataset.letter = v.letter;
      card.innerHTML = `
        <div class="letter">${{v.letter}}</div>
        <pre>${{escapeHtml(v.error ? '(помилка: ' + v.error + ')' : v.text)}}</pre>
        <textarea placeholder="нотатка (необов'язково)" data-group="${{gIdx}}" data-letter="${{v.letter}}">${{escapeHtml(gState.notes[v.letter] || '')}}</textarea>
      `;
      if (!v.error) {{
        card.addEventListener('click', (e) => {{
          // Ignore clicks on textarea
          if (e.target.tagName === 'TEXTAREA') return;
          const prev = gState.winner;
          if (prev === v.letter) {{
            gState.winner = null;
          }} else {{
            gState.winner = v.letter;
          }}
          saveState();
          render(); // re-render whole group to update highlights
        }});
      }}
      cardsDiv.appendChild(card);
    }});

    // Attach note listeners
    cardsDiv.querySelectorAll('textarea').forEach(ta => {{
      ta.addEventListener('input', (e) => {{
        const g = parseInt(e.target.dataset.group);
        const l = e.target.dataset.letter;
        state[g].notes[l] = e.target.value;
        saveState();
      }});
    }});
  }});
  updateProgress();
}}

function escapeHtml(text) {{
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}}

function buildVerdict() {{
  const result = {{}};
  GROUPS.forEach((g, idx) => {{
    const s = state[idx];
    const hasWinner = s.winner !== null;
    const hasNotes = Object.values(s.notes).some(n => n.trim() !== '');
    if (!hasWinner && !hasNotes) return;
    const key = `${{g.input_id}}|${{g.style}}`;
    const entry = {{}};
    if (hasWinner) entry.winner = s.winner;
    const notes = {{}};
    for (const [letter, note] of Object.entries(s.notes)) {{
      if (note.trim()) notes[letter] = note;
    }}
    if (Object.keys(notes).length) entry.notes = notes;
    result[key] = entry;
  }});
  return result;
}}

async function copyVerdict() {{
  const verdict = buildVerdict();
  const text = JSON.stringify(verdict, null, 2);
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    try {{
      await navigator.clipboard.writeText(text);
      alert('✅ Вердикт скопійовано!');
      return;
    }} catch (e) {{}}
  }}
  // Fallback
  const pre = document.getElementById('fallback-pre');
  pre.textContent = text;
  pre.style.display = 'block';
  alert('❌ Буфер недоступний — скопіюйте вручну з поля нижче.');
}}

// Init
loadState();
render();
</script>
</body>
</html>"""

    (out_dir / "bench_vote.html").write_text(html, encoding="utf-8")


def _write_outputs(out_dir: Path, inputs: list[dict], results: list[dict], meta: dict) -> None:
    """Write bench_results.json, bench_blind.md, bench_key.json, and bench_vote.html.
    Shuffles happen once; groups structure feeds all artefacts."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build groups (shuffle inside)
    groups = _build_groups(inputs, results)

    # Full results (unshuffled, for debugging)
    (out_dir / "bench_results.json").write_text(
        json.dumps({"meta": meta, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Blind markdown
    md = [
        "# Бенчмарк «Покращення» — сліпе оцінювання",
        "",
        "**Інструкція:** для кожного стилю оціни варіанти 1–5 (сенс збережено /",
        "стиль влучив / нічого не вигадано) і познач найкращий.",
        "**НЕ відкривай `bench_key.json` до кінця оцінювання.**",
        "",
    ]
    key: dict[str, str] = {}

    # Iterate in the same order as groups (first by input, then style)
    current_input = None
    for g in groups:
        if g["input_id"] != current_input:
            current_input = g["input_id"]
            md += [f"## Вхід {g['input_id']}", "", "```", g["input_text"], "```", ""]
        md.append(f"### Стиль: {g['style']}")
        md.append("")
        for v in g["variants"]:
            letter = v["letter"]
            key[f"{g['input_id']}|{g['style']}|{letter}"] = v["model"]
            if v["error"]:
                md.append(f"**Варіант {letter}:** — (помилка: {v['error']})")
            else:
                md += [f"**Варіант {letter}:**", "", "```", v["text"], "```"]
            md.append("")

    (out_dir / "bench_blind.md").write_text("\n".join(md), encoding="utf-8")
    (out_dir / "bench_key.json").write_text(
        json.dumps(
            {
                "_comment": "НЕ відкривати до кінця оцінювання — мапінг мітка→модель.",
                "mapping": key,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Interactive HTML voting sheet
    _write_vote_html(out_dir, inputs, groups, meta["date"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Сліпий бенч моделей для Pysar Enhance")
    parser.add_argument("--wavs", nargs="*", type=Path, default=[])
    parser.add_argument("--texts", nargs="*", type=Path, default=[])
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    parser.add_argument("--styles", nargs="*", default=DEFAULT_STYLES)
    parser.add_argument("--out", type=Path, default=Path("bench_out"))
    parser.add_argument("--lang", default="uk")
    parser.add_argument(
        "--cooldown",
        type=int,
        default=90,
        help="Fallback pause (seconds) when thermal sensor unavailable",
    )
    args = parser.parse_args()

    if not is_ollama_alive():
        print("❌ Ollama не запущений — стартуй його (напр. через KobzarAI).", file=sys.stderr)
        sys.exit(1)

    if args.wavs and not server.ensure_running(60):
        print("❌ whisper-сервер не піднявся — нема чим транскрибувати WAV.", file=sys.stderr)
        sys.exit(1)
    try:
        inputs = _collect_inputs(args.wavs, args.texts, args.lang)
    finally:
        if args.wavs:
            server.shutdown()

    if not inputs:
        print("❌ Нема жодного вхідного тексту.", file=sys.stderr)
        sys.exit(1)

    style_prompts = {s: _style_prompt(s, args.lang) for s in args.styles}
    results: list[dict] = []
    total = len(inputs) * len(args.styles) * len(args.models)
    n = 0

    therm_start = _therm_state()
    first_model = True

    try:
        for model in args.models:
            if not first_model:
                _cooldown_gate(args.cooldown)
            else:
                first_model = False

            print(f"\n🔄 Грію модель {model} …")
            preload(model)
            for inp in inputs:
                for style_key, prompt in style_prompts.items():
                    n += 1
                    print(
                        f"[{n}/{total}] {model} · {style_key} · {inp['id']} …",
                        end=" ",
                        flush=True,
                    )
                    therm_snap = _therm_state()
                    t0 = time.perf_counter()
                    out_text, err = enhance(
                        inp["text"], prompt, model, example=style_example(style_key)
                    )
                    dur = time.perf_counter() - t0
                    preamble_flag = _suspect_preamble(out_text) if not err and out_text else False
                    print(f"{dur:.1f}s {'ERR' if err else 'ok'}")
                    results.append(
                        {
                            "input_id": inp["id"],
                            "style": style_key,
                            "model": model,
                            "output_text": out_text or "",
                            "error": err,
                            "seconds": round(dur, 2),
                            "therm": therm_snap,
                            "suspect_preamble": preamble_flag,
                        }
                    )
            # Free RAM before the next candidate — 8 GB can't hold two 4B models.
            subprocess.run(["ollama", "stop", model], capture_output=True)
    except KeyboardInterrupt:
        print("\n⏸ Перервано — зберігаю те, що встигло прогнатись…", file=sys.stderr)

    therm_end = _therm_state()
    meta = {
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "models": args.models,
        "styles": {k: v for k, v in style_prompts.items()},
        "language": args.lang,
        "inputs": inputs,
        "therm_start": therm_start,
        "therm_end": therm_end,
    }
    _write_outputs(args.out, inputs, results, meta)

    # Summary of suspect preambles
    suspect_count = sum(1 for r in results if r.get("suspect_preamble"))
    print(f"\n✅ Готово: {args.out.resolve()}/bench_blind.md — суди наосліп.")
    if suspect_count > 0:
        print(f"⚠️ Підозра на преамбулу в {suspect_count} з {len(results)} результатів.")


if __name__ == "__main__":
    main()
