"""Редактор транскриптів: текст і оригінальний звук в одному вікні.

Навіщо він у застосунку, а не окремою командою (вимога Льоші, 06.09.2026):
редактор уже існував як самописний сайт дня уроків, піднімався руками й жив
поза Писарем — тобто для будь-кого, крім мене, його не існувало. Тут він
відкривається кнопкою з того самого екрана, де народився транскрипт.

Чому сторінка в браузері, а не ще одне AppKit-вікно: редагування тексту під
звук — це десятки дрібних станів (виділення, розрізання, скасування), і
переписувати їх на WKWebView-мосту означало б робити ту саму роботу двічі.
Сервер локальний (127.0.0.1), з разовим токеном у шляху, живе лише поки живе
застосунок і віддає РІВНО ті файли, які сам зареєстрував.

🔴 Межа відповідальності: редактор НІКОЛИ не чіпає оригінальний `.md` і
`.сегменти.jsonl`. Правки лягають окремим файлом `<імʼя>.правки.json`, а
готовий текст — у `<імʼя>.уточнено.md`. Втратити вихідний запис через правку
неможливо.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import os
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

_HOST = "127.0.0.1"
# 8777 і 8778 зайняті іншими локальними пультами (пульт субтитрів і сайт дня
# уроків) — беремо вільний порт у системи, щоб не зіткнутись із ними.
_PORT_ANY = 0

_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()
_docs: dict[str, dict] = {}  # id → {"md", "sidecar", "edits", "audio":[Path]}
_token = ""


# ── Джерела ───────────────────────────────────────────────────────────────────


def _read_sidecar(path: Path) -> tuple[dict, list[dict]]:
    meta: dict = {}
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if "_meta" in obj:
                meta.update(obj["_meta"])
            elif obj.get("text"):
                rows.append(obj)
    return meta, rows


def sidecar_for(md_path: Path) -> Path:
    return md_path.with_suffix(".сегменти.jsonl")


def edits_for(md_path: Path) -> Path:
    return md_path.with_suffix(".правки.json")


def speakers_for(md_path: Path) -> Path:
    return md_path.with_suffix(".спікери.json")


def can_edit(md_path: Path | str | None) -> bool:
    """Чи є з чим працювати: без міток часу редактор — просто текстовий файл."""
    if not md_path:
        return False
    side = sidecar_for(Path(md_path))
    if not side.exists():
        return False
    with contextlib.suppress(Exception):
        _meta, rows = _read_sidecar(side)
        return any(r.get("t0") is not None for r in rows)
    return False


def _audio_paths(meta: dict, md_path: Path) -> list[Path]:
    out: list[Path] = []
    for p in meta.get("audio") or []:
        with contextlib.suppress(Exception):
            path = Path(p)
            if path.exists() and path.stat().st_size > 44:
                out.append(path)
    return out


def _document(md_path: Path) -> dict:
    """Зібрати все, що потрібно сторінці: рядки, мовці, аудіо, збережені правки."""
    meta, rows = _read_sidecar(sidecar_for(md_path))
    names: dict[str, str] = {}
    spk = speakers_for(md_path)
    if spk.exists():
        with contextlib.suppress(Exception):
            data = json.loads(spk.read_text(encoding="utf-8"))
            names = data.get("names") or {}
            by_i = {r.get("i"): r.get("speaker") for r in data.get("rows") or []}
            for r in rows:
                if r.get("i") in by_i:
                    r["speaker"] = by_i[r["i"]]
    saved = None
    ed = edits_for(md_path)
    if ed.exists():
        with contextlib.suppress(Exception):
            saved = json.loads(ed.read_text(encoding="utf-8"))
    return {
        "title": md_path.stem,
        "meta": meta,
        "rows": rows,
        "names": names,
        "audio": _audio_paths(meta, md_path),
        "saved": saved,
    }


# ── Сервер ────────────────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a) -> None:  # тиша в консолі застосунку
        pass

    # -- helpers --
    def _deny(self, code: int = 404) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _doc(self, parts: list[str]) -> dict | None:
        if len(parts) < 2 or parts[0] != _token:
            return None
        return _docs.get(parts[1])

    def _send(self, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- GET --
    def do_GET(self) -> None:
        parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        doc = self._doc(parts)
        if doc is None:
            return self._deny()
        if len(parts) == 2:
            return self._send(build_page(parts[1], doc).encode("utf-8"))
        if len(parts) == 4 and parts[2] == "audio":
            with contextlib.suppress(Exception):
                return self._audio(doc, int(parts[3]))
        return self._deny()

    def _audio(self, doc: dict, index: int) -> None:
        files = doc["audio"]
        if not (0 <= index < len(files)):
            return self._deny()
        path = files[index]
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range", "")
        # 🔴 Range обовʼязковий: без нього браузер тягне весь WAV, перш ніж дати
        # перемотку, і на годинному записі перемотка просто не працює.
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            start = int(m.group(1)) if m.group(1) else 0
            end = int(m.group(2)) if m.group(2) else size - 1
            start, end = max(0, start), min(end, size - 1)
            if start > end:
                return self._deny(416)
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return None
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as f:
            while chunk := f.read(262144):
                self.wfile.write(chunk)
        return None

    # -- POST --
    def do_POST(self) -> None:
        parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        doc = self._doc(parts)
        if doc is None or len(parts) != 3:
            return self._deny()
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._deny(400)
        if parts[2] == "save":
            out = save_edits(doc["md"], payload)
            return self._send(
                json.dumps({"ok": True, "path": str(out)}).encode(), "application/json"
            )
        if parts[2] == "export":
            out = export_markdown(doc["md"], payload)
            return self._send(
                json.dumps({"ok": True, "path": str(out)}).encode(), "application/json"
            )
        return self._deny()


def save_edits(md_path: Path, payload: dict) -> Path:
    """Правки — окремий файл поруч. Оригінал не чіпаємо ніколи."""
    out = edits_for(Path(md_path))
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)  # атомарно: обірваний запис не знищить попередні правки
    return out


def export_markdown(md_path: Path, payload: dict) -> Path:
    """Готовий текст із іменами — окремим файлом `<імʼя>.уточнено.md`."""
    rows = payload.get("rows") or []
    names = {s.get("key"): (s.get("name") or "") for s in payload.get("speakers") or []}
    lines = [f"# {Path(md_path).stem} — уточнено вручну\n"]
    cur, buf, start = object(), [], 0.0
    for r in rows:
        who = r.get("who")
        if who != cur and buf:
            lines.append(_block(names, cur, start, buf))
            buf = []
        if who != cur:
            cur, start = who, float(r.get("t0") or 0.0)
        text = (r.get("text") or "").strip()
        if r.get("flag"):
            text += " ⟨неточно⟩"
        if text:
            buf.append(text)
    if buf:
        lines.append(_block(names, cur, start, buf))
    out = Path(md_path).with_suffix(".уточнено.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _block(names: dict, who, start: float, buf: list[str]) -> str:
    name = names.get(who) or "Невідомий"
    mm, ss = divmod(int(start or 0), 60)
    return f"\n**{name}** ({mm}:{ss:02d})\n\n" + " ".join(buf) + "\n"


def _ensure_server() -> str:
    global _server, _thread, _token
    with _lock:
        if _server is None:
            _token = secrets.token_urlsafe(12)
            _server = ThreadingHTTPServer((_HOST, _PORT_ANY), _Handler)
            _server.daemon_threads = True
            _thread = threading.Thread(target=_server.serve_forever, daemon=True)
            _thread.start()
        return f"http://{_HOST}:{_server.server_address[1]}"


def stop() -> None:
    global _server
    with _lock:
        if _server is not None:
            with contextlib.suppress(Exception):
                _server.shutdown()
            _server = None


def open_editor(md_path: Path | str) -> tuple[bool, str]:
    """Підняти сервер (якщо треба) і відкрити редактор для цього транскрипту."""
    md = Path(md_path)
    if not md.exists():
        return False, "транскрипт не знайдено"
    side = sidecar_for(md)
    if not side.exists():
        return False, "у цього транскрипту немає міток часу — його зроблено до 06.09.2026"
    doc = _document(md)
    if not doc["rows"]:
        return False, "у транскрипті немає сегментів"
    doc_id = secrets.token_urlsafe(8)
    doc["md"] = md
    _docs[doc_id] = doc
    base = _ensure_server()
    url = f"{base}/{_token}/{doc_id}"
    webbrowser.open(url)
    return True, url


# ── Сторінка ─────────────────────────────────────────────────────────────────


def build_page(doc_id: str, doc: dict) -> str:
    """Самодостатня сторінка: дані вшиті всередину, зовнішніх запитів нема."""
    rows = []
    for n, r in enumerate(doc["rows"]):
        rows.append(
            {
                "id": f"r{n}",
                "t0": r.get("t0"),
                "t1": r.get("t1"),
                "src": r.get("src"),
                "text": (r.get("text") or "").strip(),
                "speaker": r.get("speaker"),
            }
        )
    data = {
        "title": doc["title"],
        "rows": rows,
        "names": doc["names"],
        "audio": [
            {"name": p.name, "url": f"/{_token}/{doc_id}/audio/{i}"}
            for i, p in enumerate(doc["audio"])
        ],
        "saveUrl": f"/{_token}/{doc_id}/save",
        "exportUrl": f"/{_token}/{doc_id}/export",
        "saved": doc.get("saved"),
    }
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return _PAGE.replace("/*__DATA__*/null", payload)


_PAGE = r"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Редактор транскрипту — Pysar</title>
<style>
  :root{
    --bg:#fbfbfa; --panel:#ffffff; --fg:#1b1b1d; --dim:#77767c; --line:#e6e5e2;
    --soft:#f2f1ee; --accent:#2f6fd0; --flag:#b8442c; --ok:#2f7a44;
    --now:#fff6d8; --sel:#2f6fd0;
  }
  @media(prefers-color-scheme:dark){
    :root{--bg:#151517; --panel:#1c1c1f; --fg:#e9e8e5; --dim:#95949a; --line:#2b2b30;
      --soft:#232327; --accent:#6ea6f0; --flag:#e2795f; --ok:#79bb8d;
      --now:#3a3320; --sel:#6ea6f0;}
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font:15px/1.6 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;
    -webkit-font-smoothing:antialiased}
  button,input,select{font:inherit;color:inherit}
  button{background:transparent;border:1px solid var(--line);border-radius:8px;
    padding:5px 11px;cursor:pointer;transition:background .12s,border-color .12s,transform .06s}
  button:hover{background:var(--soft)}
  button:active{transform:translateY(1px)}
  button.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
  header{position:sticky;top:0;z-index:5;background:var(--bg);
    border-bottom:1px solid var(--line);padding:12px 24px 10px}
  .top{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
  h1{margin:0;font-size:15px;font-weight:600;letter-spacing:-.01em}
  .muted{color:var(--dim);font-size:12.5px}
  .player{display:flex;align-items:center;gap:8px;margin-top:9px;flex-wrap:wrap}
  .scrub{flex:1;min-width:220px;height:22px;appearance:none;background:transparent}
  .scrub::-webkit-slider-runnable-track{height:4px;border-radius:99px;background:var(--line)}
  .scrub::-webkit-slider-thumb{appearance:none;width:13px;height:13px;margin-top:-4.5px;
    border-radius:50%;background:var(--accent)}
  .time{font-variant-numeric:tabular-nums;font-size:12.5px;color:var(--dim);min-width:96px}
  .spk{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:9px}
  .chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);
    border-radius:99px;padding:3px 10px;font-size:13px;cursor:pointer;background:var(--panel)}
  .chip .k{font-variant-numeric:tabular-nums;font-size:11px;color:var(--dim);
    border:1px solid var(--line);border-radius:4px;padding:0 4px}
  .chip.dot::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--c,#999)}
  .chip .more{color:var(--dim);padding:0 2px}
  .selline{margin-top:8px;font-size:12.5px;color:var(--dim);
    display:flex;gap:8px;align-items:center;min-height:20px}
  .selline b{color:var(--fg);font-weight:600}
  main{max-width:1100px;margin:0 auto;padding:10px 24px 55vh}
  .row{display:flex;gap:12px;padding:7px 10px;margin:0 -10px;border-radius:9px;
    align-items:flex-start;position:relative}
  .row:hover{background:var(--soft)}
  .row.now{background:var(--now)}
  .row.sel{box-shadow:inset 0 0 0 2px var(--sel)}
  .row.flag .tx{text-decoration:underline wavy var(--flag) 1px;text-underline-offset:4px}
  .tc{flex:0 0 52px;font-variant-numeric:tabular-nums;font-size:12.5px;color:var(--dim);
    cursor:pointer;padding-top:3px}
  .who{flex:0 0 auto;max-width:190px;font-size:12.5px;padding:1px 8px;border-radius:99px;
    border:1px solid var(--line);cursor:pointer;white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis;background:var(--panel)}
  .who.none{color:var(--dim);border-style:dashed}
  .tx{flex:1;min-width:0;outline:none;padding:1px 3px;border-radius:6px;white-space:pre-wrap}
  .tx:focus{box-shadow:0 0 0 2px var(--accent);background:var(--panel)}
  .tools{display:none;gap:4px;position:absolute;right:8px;top:4px}
  .row:hover .tools,.row.sel .tools{display:flex}
  .tools button{font-size:11.5px;padding:2px 7px;border-radius:6px;color:var(--dim);
    background:var(--panel)}
  .empty{color:var(--dim);font-style:italic}
  .pop{position:absolute;z-index:20;background:var(--panel);border:1px solid var(--line);
    border-radius:11px;padding:10px;box-shadow:0 12px 30px -12px rgba(0,0,0,.35);
    display:flex;flex-direction:column;gap:7px;min-width:230px}
  .pop input,.pop select{border:1px solid var(--line);border-radius:7px;padding:5px 8px;
    background:var(--bg)}
  .pop .rowb{display:flex;gap:6px;justify-content:space-between}
  footer{position:fixed;bottom:0;left:0;right:0;background:var(--bg);
    border-top:1px solid var(--line);padding:7px 24px;font-size:12px;color:var(--dim);
    display:flex;gap:14px;flex-wrap:wrap;align-items:center}
  kbd{border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:11px;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .toast{position:fixed;left:50%;transform:translateX(-50%);bottom:46px;z-index:30;
    background:var(--fg);color:var(--bg);padding:7px 14px;border-radius:99px;font-size:13px;
    opacity:0;transition:opacity .18s;pointer-events:none}
  .toast.on{opacity:1}
</style>
</head>
<body>
<header>
  <div class="top">
    <h1 id="title"></h1>
    <span class="muted" id="counts"></span>
    <span class="muted" id="save" style="margin-left:auto"></span>
  </div>
  <div class="player">
    <button id="play">Пуск</button>
    <button id="b15">−15 с</button>
    <button id="f15">+15 с</button>
    <input type="range" class="scrub" id="scrub" min="0" max="1000" value="0">
    <span class="time" id="tnow">0:00 / 0:00</span>
    <select id="rate">
      <option value="0.75">0,75×</option><option value="1" selected>1×</option>
      <option value="1.25">1,25×</option><option value="1.5">1,5×</option>
      <option value="1.75">1,75×</option><option value="2">2×</option>
    </select>
    <select id="track" style="display:none"></select>
    <button id="follow" class="on">Стежити</button>
  </div>
  <div class="spk" id="spk"></div>
  <div class="selline" id="selline"></div>
</header>
<main id="list"></main>
<footer>
  <span><kbd>пробіл</kbd> пуск/пауза</span>
  <span><kbd>←</kbd><kbd>→</kbd> ±5 с · <kbd>⇧</kbd> ±15 с</span>
  <span><kbd>1</kbd>…<kbd>9</kbd> автор обраного рядка</span>
  <span><kbd>Enter</kbd> у тексті — розрізати</span>
  <span><kbd>⌘Z</kbd> скасувати</span>
  <button id="export" style="margin-left:auto">Зібрати чистовик</button>
</footer>
<div class="toast" id="toast"></div>
<script>
const DATA = /*__DATA__*/null;
const $ = (id) => document.getElementById(id);
const PALETTE = ["#2f6fd0","#b8442c","#2f7a44","#8a4fb0","#b07a1e","#1f7f8f","#a03a6b","#4b6a2f","#7a5230"];

// ── Стан ────────────────────────────────────────────────────────────────────
// Документ живий у памʼяті цілком: рядки + мовці. Кожна структурна зміна
// пише знімок в історію — саме її бракувало, коли ⌘Z не скасовував випадково
// перепризначеного автора (скарга Льоші 06.09.2026).
let S = {rows: [], speakers: [], sel: null};
let history = [], future = [], saveTimer = null, lastNow = null, userScrolled = 0;

function snapshot(){ return JSON.stringify({rows: S.rows, speakers: S.speakers}); }
function pushHistory(){
  history.push(snapshot());
  if (history.length > 200) history.shift();
  future = [];
}
function restore(json){
  const d = JSON.parse(json);
  S.rows = d.rows; S.speakers = d.speakers;
  if (S.sel && !S.rows.some(r => r.id === S.sel)) S.sel = null;
  render(); scheduleSave();
}
function undo(){
  if (!history.length) return toast("Скасовувати нічого");
  future.push(snapshot()); restore(history.pop()); toast("Скасовано");
}
function redo(){
  if (!future.length) return;
  history.push(snapshot()); restore(future.pop()); toast("Повернуто");
}

function init(){
  const saved = DATA.saved;
  if (saved && saved.rows && saved.rows.length) {
    S.rows = saved.rows;
    S.speakers = saved.speakers || [];
  } else {
    const keys = [];
    DATA.rows.forEach(r => { if (r.speaker && !keys.includes(r.speaker)) keys.push(r.speaker); });
    S.speakers = keys.map((k, i) => ({
      key: k, name: DATA.names[k] || ("Спікер " + (i + 1)),
      hot: i < 9 ? String(i + 1) : "", color: PALETTE[i % PALETTE.length],
    }));
    S.rows = DATA.rows.map(r => ({
      id: r.id, t0: r.t0, t1: r.t1, src: r.src,
      text: r.text, who: r.speaker || null, flag: false,
    }));
  }
  $("title").textContent = DATA.title;
}

// ── Аудіо ───────────────────────────────────────────────────────────────────
const audio = new Audio();
audio.preload = "metadata";
let trackIndex = 0;
function loadTrack(i, keepTime){
  if (!DATA.audio.length) return;
  const t = keepTime ? audio.currentTime : 0;
  trackIndex = i;
  audio.src = DATA.audio[i].url;
  audio.addEventListener("loadedmetadata", () => { if (t) audio.currentTime = t; }, {once: true});
}
function fmt(s){
  s = Math.max(0, Math.floor(s || 0));
  const m = Math.floor(s / 60), ss = s % 60;
  return m + ":" + String(ss).padStart(2, "0");
}
function seek(t){ if (DATA.audio.length) audio.currentTime = Math.max(0, t); }
function toggle(){ if (!DATA.audio.length) return toast("Аудіо до цього запису не збереглося");
  if (audio.paused) audio.play(); else audio.pause(); }

// ── Мовці ───────────────────────────────────────────────────────────────────
function speakerOf(row){ return S.speakers.find(s => s.key === row.who) || null; }
function newKey(){ return "s" + Date.now().toString(36) + Math.floor(Math.random() * 99); }
function addSpeaker(name){
  const used = S.speakers.map(s => s.hot);
  let hot = "";
  for (let n = 1; n <= 9; n++) if (!used.includes(String(n))) { hot = String(n); break; }
  const sp = {key: newKey(), name: name, hot: hot, color: PALETTE[S.speakers.length % PALETTE.length]};
  pushHistory(); S.speakers.push(sp); render(); scheduleSave();
  return sp;
}
function assign(rowId, key){
  const row = S.rows.find(r => r.id === rowId);
  if (!row) return;
  pushHistory();
  row.who = (row.who === key) ? null : key;
  render(); scheduleSave();
  const sp = S.speakers.find(s => s.key === row.who);
  toast((sp ? sp.name : "Без автора") + " → " + fmt(row.t0));
}

function renderSpeakers(){
  const box = $("spk"); box.textContent = "";
  S.speakers.forEach(sp => {
    const c = document.createElement("span");
    c.className = "chip dot"; c.style.setProperty("--c", sp.color);
    if (sp.hot) { const k = document.createElement("span"); k.className = "k"; k.textContent = sp.hot; c.appendChild(k); }
    const n = document.createElement("span"); n.textContent = sp.name; c.appendChild(n);
    const more = document.createElement("span"); more.className = "more"; more.textContent = "···";
    more.title = "Перейменувати, змінити клавішу, видалити";
    more.addEventListener("click", (e) => { e.stopPropagation(); speakerPopup(sp, c); });
    c.appendChild(more);
    // Клік по імені = поставити його ОБРАНОМУ рядку. Той самий шлях, що й
    // клавішею, лише без клавіші — Льоша знайшов його сам і назвав «ок».
    c.addEventListener("click", () => {
      if (!S.sel) return toast("Спершу тицьни в репліку");
      assign(S.sel, sp.key);
    });
    box.appendChild(c);
  });
  const add = document.createElement("button");
  add.textContent = "＋ автор";
  add.addEventListener("click", () => {
    const sp = addSpeaker("Новий автор");
    const chip = box.querySelectorAll(".chip")[S.speakers.length - 1];
    speakerPopup(sp, chip || add);
  });
  box.appendChild(add);
}

function speakerPopup(sp, anchor){
  closePopups();
  const p = document.createElement("div"); p.className = "pop";
  const name = document.createElement("input"); name.value = sp.name; name.placeholder = "Імʼя";
  const hot = document.createElement("select");
  const none = document.createElement("option"); none.value = ""; none.textContent = "без клавіші";
  hot.appendChild(none);
  for (let n = 1; n <= 9; n++) {
    const o = document.createElement("option"); o.value = String(n);
    const taken = S.speakers.find(x => x.hot === String(n) && x.key !== sp.key);
    o.textContent = "клавіша " + n + (taken ? " (зайнята: " + taken.name + ")" : "");
    hot.appendChild(o);
  }
  hot.value = sp.hot || "";
  const bar = document.createElement("div"); bar.className = "rowb";
  const del = document.createElement("button"); del.textContent = "Видалити";
  const ok = document.createElement("button"); ok.textContent = "Готово"; ok.className = "on";
  del.addEventListener("click", () => {
    pushHistory();
    S.rows.forEach(r => { if (r.who === sp.key) r.who = null; });
    S.speakers = S.speakers.filter(x => x.key !== sp.key);
    closePopups(); render(); scheduleSave(); toast("Автора видалено");
  });
  const apply = () => {
    pushHistory();
    sp.name = name.value.trim() || sp.name;
    // Клавішу віддаємо новому власнику: два автори на одній цифрі — це той
    // самий «тисну хоткей, а воно йде не туди», лише з іншого боку.
    if (hot.value) S.speakers.forEach(x => { if (x !== sp && x.hot === hot.value) x.hot = ""; });
    sp.hot = hot.value;
    closePopups(); render(); scheduleSave();
  };
  ok.addEventListener("click", apply);
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });
  bar.appendChild(del); bar.appendChild(ok);
  p.appendChild(name); p.appendChild(hot); p.appendChild(bar);
  document.body.appendChild(p);
  const r = anchor.getBoundingClientRect();
  p.style.left = Math.min(r.left, window.innerWidth - 260) + "px";
  p.style.top = (r.bottom + window.scrollY + 6) + "px";
  name.focus(); name.select();
}
function closePopups(){ document.querySelectorAll(".pop").forEach(n => n.remove()); }
document.addEventListener("click", (e) => {
  if (!e.target.closest(".pop") && !e.target.closest(".more")) closePopups();
});

// ── Рядки ───────────────────────────────────────────────────────────────────
function select(id){
  S.sel = id;
  document.querySelectorAll(".row").forEach(n => n.classList.toggle("sel", n.dataset.id === id));
  renderSelLine();
}
function renderSelLine(){
  const box = $("selline");
  const row = S.rows.find(r => r.id === S.sel);
  if (!row) { box.textContent = "Обраної репліки немає — тицьни в рядок, і клавіші 1…9 працюватимуть по ньому."; return; }
  const sp = speakerOf(row);
  box.textContent = "";
  const a = document.createElement("span"); a.textContent = "Обрано " + fmt(row.t0) + " · ";
  const b = document.createElement("b"); b.textContent = sp ? sp.name : "без автора";
  const c = document.createElement("span");
  c.textContent = " · " + (row.text || "").slice(0, 60) + ((row.text || "").length > 60 ? "…" : "");
  box.appendChild(a); box.appendChild(b); box.appendChild(c);
}

function rowNode(row, idx){
  const n = document.createElement("div");
  n.className = "row" + (row.flag ? " flag" : "") + (row.id === S.sel ? " sel" : "");
  n.dataset.id = row.id;

  const tc = document.createElement("div"); tc.className = "tc"; tc.textContent = fmt(row.t0);
  tc.title = "Перемотати сюди";
  tc.addEventListener("click", () => { seek(row.t0 || 0); select(row.id); });
  n.appendChild(tc);

  const sp = speakerOf(row);
  const who = document.createElement("div");
  who.className = "who" + (sp ? "" : " none");
  who.textContent = sp ? sp.name : "хто це?";
  if (sp) { who.style.borderColor = sp.color; who.style.color = sp.color; }
  who.title = "Обрати автора";
  who.addEventListener("click", (e) => { e.stopPropagation(); select(row.id); whoPopup(row, who); });
  n.appendChild(who);

  const tx = document.createElement("div");
  tx.className = "tx" + (row.text ? "" : " empty");
  tx.contentEditable = "true"; tx.spellcheck = false;
  tx.textContent = row.text || "";
  tx.dataset.ph = "(що почув, але Whisper не взяв)";
  tx.addEventListener("focus", () => select(row.id));
  tx.addEventListener("input", () => { row.text = tx.textContent; scheduleSave(); });
  tx.addEventListener("blur", () => { renderSelLine(); });
  tx.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); splitAt(row, tx); }
  });
  n.appendChild(tx);

  const tools = document.createElement("div"); tools.className = "tools";
  const mk = (label, title, fn) => {
    const b = document.createElement("button"); b.textContent = label; b.title = title;
    b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
    tools.appendChild(b);
  };
  mk("розрізати", "Розрізати в місці курсора (Enter)", () => splitAt(row, tx));
  mk("＋ рядок", "Вставити порожній рядок нижче — для того, що не потрапило в запис", () => insertAfter(idx));
  mk(row.flag ? "зняти мітку" : "неточно", "Позначити місце як сумнівне", () => {
    pushHistory(); row.flag = !row.flag; render(); scheduleSave();
  });
  if (row.added) mk("видалити", "Видалити цей рядок", () => {
    pushHistory(); S.rows = S.rows.filter(r => r !== row); render(); scheduleSave();
  });
  n.appendChild(tools);

  n.addEventListener("mousedown", (e) => { if (!e.target.closest(".tx")) select(row.id); });
  return n;
}

function whoPopup(row, anchor){
  closePopups();
  const p = document.createElement("div"); p.className = "pop";
  S.speakers.forEach(sp => {
    const b = document.createElement("button");
    b.textContent = (sp.hot ? sp.hot + " · " : "") + sp.name;
    b.addEventListener("click", () => { closePopups(); assign(row.id, sp.key); });
    p.appendChild(b);
  });
  const clear = document.createElement("button"); clear.textContent = "зняти автора";
  clear.addEventListener("click", () => { closePopups(); pushHistory(); row.who = null; render(); scheduleSave(); });
  p.appendChild(clear);
  const inp = document.createElement("input"); inp.placeholder = "новий автор + Enter";
  inp.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !inp.value.trim()) return;
    const sp = addSpeaker(inp.value.trim());
    closePopups(); assign(row.id, sp.key);
  });
  p.appendChild(inp);
  document.body.appendChild(p);
  const r = anchor.getBoundingClientRect();
  p.style.left = Math.min(r.left, window.innerWidth - 260) + "px";
  p.style.top = (r.bottom + window.scrollY + 6) + "px";
  inp.focus();
}

// 🔴 Розрізати можна СКІЛЬКИ ЗАВГОДНО разів: обидві половини — звичайні рядки
// з власним часом, автором і текстом. Одноразове розрізання було головною
// скаргою: «кажу я, потім інша дитина, потім знову я» не вміщалось у два шматки.
function splitAt(row, tx){
  const sel = window.getSelection();
  let off = (row.text || "").length;
  if (sel && sel.rangeCount && tx.contains(sel.anchorNode)) off = sel.anchorOffset;
  const text = tx.textContent;
  const left = text.slice(0, off).trim(), right = text.slice(off).trim();
  if (!left || !right) return toast("Постав курсор туди, де починає інший");
  pushHistory();
  const i = S.rows.indexOf(row);
  const t0 = row.t0 || 0, t1 = (row.t1 == null ? t0 : row.t1);
  const cut = t0 + (t1 - t0) * (off / Math.max(1, text.length));
  const second = {id: row.id + "b" + Date.now().toString(36).slice(-4),
    t0: Math.round(cut * 100) / 100, t1: t1, src: row.src, text: right,
    who: null, flag: false, added: true};
  row.text = left; row.t1 = second.t0;
  S.rows.splice(i + 1, 0, second);
  render(); select(second.id); scheduleSave();
  toast("Розрізано — познач автора другої частини");
}

function insertAfter(idx){
  pushHistory();
  const prev = S.rows[idx], next = S.rows[idx + 1];
  const t0 = prev ? (prev.t1 == null ? prev.t0 : prev.t1) : 0;
  const t1 = next ? next.t0 : t0;
  const row = {id: "n" + Date.now().toString(36), t0: t0, t1: t1, src: prev ? prev.src : null,
    text: "", who: null, flag: false, added: true};
  S.rows.splice(idx + 1, 0, row);
  render(); select(row.id);
  const node = document.querySelector('[data-id="' + row.id + '"] .tx');
  if (node) node.focus();
  scheduleSave();
}

function render(){
  renderSpeakers();
  const list = $("list");
  list.textContent = "";
  const frag = document.createDocumentFragment();
  S.rows.forEach((r, i) => frag.appendChild(rowNode(r, i)));
  list.appendChild(frag);
  $("counts").textContent = S.rows.length + " реплік · "
    + S.rows.filter(r => !r.who).length + " без автора";
  renderSelLine();
  markNow(true);
}

// ── Збереження ──────────────────────────────────────────────────────────────
function scheduleSave(){
  clearTimeout(saveTimer);
  $("save").textContent = "…";
  saveTimer = setTimeout(save, 700);
}
async function save(){
  try {
    await fetch(DATA.saveUrl, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({version: 1, rows: S.rows, speakers: S.speakers})});
    const d = new Date();
    $("save").textContent = "збережено " + d.getHours() + ":" + String(d.getMinutes()).padStart(2, "0");
  } catch (e) { $("save").textContent = "не зберігається: " + e; }
}
$("export").addEventListener("click", async () => {
  const r = await fetch(DATA.exportUrl, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({rows: S.rows, speakers: S.speakers})});
  const j = await r.json();
  toast("Чистовик: " + (j.path || "").split("/").pop());
});

let toastTimer = null;
function toast(msg){
  const t = $("toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("on"), 1900);
}

// ── Підсвітка поточної репліки ──────────────────────────────────────────────
function markNow(force){
  const t = audio.currentTime;
  let cur = null;
  for (const r of S.rows) {
    if (r.t0 != null && r.t0 <= t && (r.t1 == null ? r.t0 + 3 : r.t1) >= t) { cur = r.id; break; }
  }
  if (cur === lastNow && !force) return;
  lastNow = cur;
  document.querySelectorAll(".row").forEach(n => n.classList.toggle("now", n.dataset.id === cur));
  if (cur && $("follow").classList.contains("on") && Date.now() - userScrolled > 4000) {
    const n = document.querySelector('[data-id="' + cur + '"]');
    if (n) {
      const r = n.getBoundingClientRect();
      if (r.top < 140 || r.bottom > window.innerHeight - 80) n.scrollIntoView({block: "center", behavior: "smooth"});
    }
  }
}
window.addEventListener("scroll", () => { userScrolled = Date.now(); });

// ── Керування ───────────────────────────────────────────────────────────────
$("play").addEventListener("click", toggle);
$("b15").addEventListener("click", () => seek(audio.currentTime - 15));
$("f15").addEventListener("click", () => seek(audio.currentTime + 15));
$("rate").addEventListener("change", () => { audio.playbackRate = parseFloat($("rate").value); });
$("follow").addEventListener("click", () => $("follow").classList.toggle("on"));
$("scrub").addEventListener("input", () => {
  if (audio.duration) seek(audio.duration * ($("scrub").value / 1000));
});
audio.addEventListener("play", () => { $("play").textContent = "Пауза"; });
audio.addEventListener("pause", () => { $("play").textContent = "Пуск"; });
audio.addEventListener("timeupdate", () => {
  $("tnow").textContent = fmt(audio.currentTime) + " / " + fmt(audio.duration || 0);
  if (audio.duration) $("scrub").value = String(Math.round(audio.currentTime / audio.duration * 1000));
  markNow(false);
});

document.addEventListener("keydown", (e) => {
  const typing = e.target.isContentEditable || /input|select|textarea/i.test(e.target.tagName);
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
    e.preventDefault(); if (e.shiftKey) redo(); else undo(); return;
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); return; }
  if (typing) { if (e.key === "Escape") e.target.blur(); return; }
  if (e.key === " ") { e.preventDefault(); toggle(); return; }
  if (e.key === "ArrowLeft") { e.preventDefault(); seek(audio.currentTime - (e.shiftKey ? 15 : 5)); return; }
  if (e.key === "ArrowRight") { e.preventDefault(); seek(audio.currentTime + (e.shiftKey ? 15 : 5)); return; }
  if (e.key === "Enter" && S.sel) {
    const n = document.querySelector('[data-id="' + S.sel + '"] .tx');
    if (n) { e.preventDefault(); n.focus(); }
    return;
  }
  if (/^[1-9]$/.test(e.key)) {
    const sp = S.speakers.find(x => x.hot === e.key);
    if (!sp) return toast("Клавіша " + e.key + " ні за ким не закріплена");
    // 🔴 Клавіша ЗАВЖДИ б'є в обраний рядок, і він завжди підсвічений. Раніше
    // вона летіла в «раніше обраний», якого вже не було видно, і людина мовчки
    // переписувала старі цитати (скарга Льоші 06.09.2026).
    let target = S.sel;
    if (!target) {
      target = lastNow;
      if (!target) return toast("Тицьни в репліку — клавіша працює по обраній");
      select(target);
    }
    e.preventDefault();
    assign(target, sp.key);
  }
});

// ── Старт ───────────────────────────────────────────────────────────────────
init();
if (DATA.audio.length) {
  loadTrack(0, false);
  if (DATA.audio.length > 1) {
    const t = $("track"); t.style.display = "";
    DATA.audio.forEach((a, i) => {
      const o = document.createElement("option"); o.value = String(i);
      o.textContent = a.name.includes("-mic") ? "мікрофон" : (a.name.includes("-sys") ? "система" : a.name);
      t.appendChild(o);
    });
    t.addEventListener("change", () => loadTrack(parseInt(t.value, 10), true));
  }
} else {
  $("play").disabled = true;
  $("tnow").textContent = "аудіо не збереглося";
}
render();
</script>
</body>
</html>
"""
