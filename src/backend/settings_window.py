"""Native Settings window — an NSWindow hosting a WKWebView.

Why a WebView and not hand-laid NSViews: the panel (general settings + the
drill-in profile editor) is far less code as one styled HTML document, and it
matches the project's web-first visual language. The WebContent process only
exists while the window is open.

This module is split so the logic is testable without AppKit:
  • build_html(state)        — pure: renders the document from a state dict
  • dispatch(msg, handlers)  — pure: routes a JS→Python message to a handler
  • SettingsWindow           — thin glue (NSWindow + WKWebView + bridge), only
                               touched at runtime, never in tests

After the first paint the window is never reloaded: state changes are pushed in
with evaluateJavaScript(window.creamApply(...)), so the user keeps their place
(e.g. inside the Profiles screen) across an edit/delete/import.

The heavy AppKit/WebKit imports are lazy (inside SettingsWindow) so importing
this module — e.g. from the test suite — needs nothing but the stdlib.
"""

import contextlib
import json
from collections.abc import Callable

# ── Pure core (testable) ─────────────────────────────────────────────────────


def dispatch(msg: dict, handlers: dict[str, Callable]) -> None:
    """Route one decoded JS message to its handler.

    A message is ``{"action": <name>}`` with an optional ``"value"``. Handlers
    that carry a value (toggles, selects) are called with it; valueless ones
    (a button press) are called with no args. Unknown actions are ignored, so a
    stale front-end can never crash the back-end.
    """
    action = msg.get("action")
    handler = handlers.get(action)
    if handler is None:
        return
    if "value" in msg:
        handler(msg["value"])
    else:
        handler()


def build_html(state: dict) -> str:
    """Render the settings document. ``state`` carries the general settings plus
    the profile library (profiles, active_profiles, current_lang, token_budget).
    """
    return _TEMPLATE.replace("/*__STATE__*/null", _encode(state))


def _encode(state: dict) -> str:
    """JSON for embedding inside a <script>. Escapes "<" so the payload can't
    break out of the tag (covers "</script>" and the U+2028/2029 edge cases)."""
    return json.dumps(state, ensure_ascii=False).replace("<", "\\u003c")


# ── HTML/CSS/JS asset ────────────────────────────────────────────────────────
# Single document, system font (SF), zinc neutrals, one calm accent, light/dark
# aware. Labels above controls; no emoji in the chrome.
_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{
    --bg:#fbfbfc; --panel:#ffffff; --line:#e7e7ea; --line-strong:#d6d6db;
    --ink:#1c1d21; --muted:#71727a; --accent:AccentColor; --accent-ink:#ffffff;
    --danger:#e5484d; --radius:12px;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#1b1c1f; --panel:#26272b; --line:#34353a; --line-strong:#3f4046;
      --ink:#ececee; --muted:#9a9ba3; --accent:AccentColor; --accent-ink:#10131a;
      --danger:#ff6369;
    }
  }
  *{box-sizing:border-box}
  html,body{margin:0; height:100%}
  ::-webkit-scrollbar{width:9px;height:9px}
  ::-webkit-scrollbar-thumb{background:var(--line-strong); border-radius:999px;
    border:2px solid transparent; background-clip:padding-box}
  body{
    font:13px/1.45 -apple-system, system-ui, sans-serif;
    color:var(--ink); background:var(--bg);
    -webkit-font-smoothing:antialiased;
    user-select:none; -webkit-user-select:none;
  }
  .screen{display:none; padding:18px 20px 24px}
  .screen.on{display:block}

  header{display:flex; align-items:center; gap:10px; margin:0 2px 16px; min-height:24px}
  header h1{font-size:17px; font-weight:600; letter-spacing:-.02em; margin:0; flex:1}
  header .sub{font-size:12px; color:var(--muted)}
  .back{
    display:inline-flex; align-items:center; gap:3px; cursor:pointer;
    font-weight:400; color:var(--accent); padding:2px 4px 2px 0; margin-left:-2px;
    border-radius:6px;
  }
  .back:hover{opacity:.7}
  .back svg{width:11px; height:11px}

  .sec-title{
    font-size:11px; font-weight:600; letter-spacing:.04em; text-transform:uppercase;
    color:var(--muted); margin:16px 2px 8px;
  }
  section{
    background:var(--panel); border:1px solid var(--line);
    border-radius:var(--radius); padding:4px 14px; margin-bottom:12px;
  }
  .row{
    display:flex; align-items:center; gap:14px;
    padding:11px 0; border-top:1px solid var(--line);
  }
  .row:first-child{border-top:0}
  .row .body{flex:1; min-width:0}
  .row .label{font-weight:500}
  .row .help{color:var(--muted); font-size:12px; margin-top:2px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  .row.disabled{opacity:.45; pointer-events:none}
  .row.nav{cursor:pointer}
  .row.nav:hover{background:color-mix(in srgb, var(--accent) 7%, transparent);
    margin:0 -14px; padding-left:14px; padding-right:14px; border-radius:8px}
  .chev{color:var(--muted); font-size:15px}

  /* Toggle */
  .toggle{position:relative; width:38px; height:22px; flex:0 0 auto}
  .toggle input{opacity:0; width:100%; height:100%; margin:0; cursor:pointer}
  .toggle .track{position:absolute; inset:0; background:var(--line-strong);
    border-radius:999px; transition:background .18s ease; pointer-events:none}
  .toggle .knob{position:absolute; top:2px; left:2px; width:18px; height:18px;
    background:#fff; border-radius:50%; transition:transform .18s ease;
    box-shadow:0 1px 2px rgba(0,0,0,.25); pointer-events:none}
  .toggle input:checked + .track{background:var(--accent)}
  .toggle input:checked + .track + .knob{transform:translateX(16px)}

  select{
    appearance:none; -webkit-appearance:none; font:inherit; color:var(--ink);
    background:var(--bg); border:1px solid var(--line-strong); border-radius:8px;
    padding:6px 28px 6px 10px; min-width:140px; max-width:200px; cursor:pointer;
    background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6'><path d='M1 1l4 4 4-4' fill='none' stroke='%2371727a' stroke-width='1.5'/></svg>");
    background-repeat:no-repeat; background-position:right 10px center;
  }
  select:focus{outline:2px solid var(--accent); outline-offset:1px}

  button{
    font:inherit; font-weight:500; color:var(--ink);
    background:var(--bg); border:1px solid var(--line-strong); border-radius:8px;
    padding:6px 12px; cursor:pointer; transition:transform .08s ease;
  }
  button:hover{border-color:var(--accent)}
  button:active{transform:translateY(1px)}
  button.primary{background:var(--accent); color:var(--accent-ink); border-color:transparent}
  button.primary:hover{filter:brightness(1.06)}
  button.danger{color:var(--danger); border-color:transparent; background:transparent}
  button.danger:hover{border-color:var(--danger)}
  button.ghost{background:transparent}

  kbd{font:inherit; font-weight:600; background:var(--bg);
    border:1px solid var(--line-strong); border-bottom-width:2px;
    border-radius:6px; padding:2px 7px; color:var(--ink)}

  .kslist{display:flex; flex-direction:column; gap:7px; margin-top:8px}
  .kslist .ksrow{display:flex; align-items:center; gap:9px;
    color:var(--muted); font-size:12px}
  .kslist .ksrow .kslabel{flex:1}
  .kslist kbd{min-width:46px; text-align:center; font-size:12px}
  .kslist kbd.muted{opacity:.5; border-style:dashed; font-weight:400}
  .clearbtn{min-width:0; padding:4px 7px; color:var(--muted)}
  .clearbtn:hover{border-color:var(--danger); color:var(--danger)}
  .hkcap{display:flex; align-items:center; gap:8px}
  .hkcap kbd{min-width:60px; text-align:center}
  .iconbtn{padding:4px 9px; font-size:12px}

  /* Profiles screen */
  .pgroup{margin-bottom:14px}
  .plang{display:flex; align-items:center; gap:10px; margin:0 2px 6px}
  .plang .name{font-weight:600; letter-spacing:-.01em}
  .plang .meter{flex:1; height:5px; border-radius:999px; background:var(--line-strong);
    overflow:hidden; min-width:40px}
  .plang .meter i{display:block; height:100%; background:var(--accent); width:0;
    transition:width .2s ease}
  .plang .meter.over i{background:var(--danger)}
  .plang .count{font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums}
  .prow{display:flex; align-items:center; gap:10px; padding:8px 0; border-top:1px solid var(--line)}
  .prow:first-child{border-top:0}
  .prow .pname{flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  .prow .pname.off{color:var(--muted)}
  .iconbtn{padding:4px 9px; font-size:12px}
  .addbtn{width:100%; font-size:12px; margin-top:4px}

  .pform{margin:8px 0 4px; padding:13px; border:1px solid var(--line-strong);
    border-radius:10px; background:var(--bg)}
  .pform label{display:block; font-size:11px; color:var(--muted); margin:0 0 4px;
    text-transform:uppercase; letter-spacing:.04em; font-weight:600}
  .pform input[type=text], .pform textarea{
    width:100%; font:inherit; color:var(--ink); background:var(--panel);
    border:1px solid var(--line-strong); border-radius:8px; padding:7px 9px;
    margin-bottom:11px; resize:vertical}
  .pform textarea{min-height:70px; line-height:1.4}
  .pform input:focus, .pform textarea:focus{outline:2px solid var(--accent); outline-offset:1px}
  .pform .frow{display:flex; gap:9px; align-items:center; justify-content:flex-end}
  .pform .frow .est{margin-right:auto; font-size:11px; color:var(--muted)}

  /* Profile sets */
  .srow{display:flex; align-items:center; gap:10px; padding:9px 0; border-top:1px solid var(--line)}
  .srow:first-child{border-top:0}
  .srow kbd{min-width:46px; text-align:center; font-size:12px; flex:0 0 auto}
  .srow .sbody{flex:1; min-width:0}
  .srow .sname{font-weight:500}
  .srow .smeta{color:var(--muted); font-size:12px; margin-top:2px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  .srow .spill{flex:0 0 auto; font-size:11px; font-weight:600; color:var(--accent);
    border:1px solid var(--accent); border-radius:999px; padding:2px 10px}
  kbd.on{background:var(--accent); color:var(--accent-ink); border-color:transparent}
  .kslist kbd.on{border-color:transparent}
  .setmembers{display:flex; flex-direction:column; gap:7px; max-height:210px;
    overflow:auto; margin-bottom:11px; padding:2px}
  .setmembers label{display:flex; align-items:center; gap:9px; margin:0;
    font-size:13px; font-weight:400; color:var(--ink); text-transform:none;
    letter-spacing:0; cursor:pointer}
  .setmembers .mlang{margin-left:auto; font-size:11px; color:var(--muted)}
  .setmembers .none{color:var(--muted); font-size:12px}

  .ai{font-size:12px; color:var(--muted); margin:2px 2px 10px; line-height:1.5}
  .notice{font-size:12px; color:var(--accent); margin:0 2px 10px; min-height:0}
</style>
</head>
<body>
  <!-- ── Main screen ──────────────────────────────────────────────────────── -->
  <div id="screen-main" class="screen on">
    <header><h1 data-i18n="settings">Settings</h1><span class="sub">Cream Typer Custom</span></header>

    <div class="sec-title" data-i18n="sec.audio">Audio</div>
    <section>
      <div class="row">
        <div class="body"><div class="label" data-i18n="mic.label">Microphone</div>
          <div class="help" data-i18n="mic.help">Input device used for dictation</div></div>
        <select id="mic"></select>
      </div>
    </section>

    <div class="sec-title" data-i18n="sec.recordings">Recordings</div>
    <section>
      <div class="row">
        <div class="body"><div class="label" data-i18n="save.label">Save recordings</div>
          <div class="help" data-i18n="save.help">Keep each WAV on disk so a failed run is recoverable</div></div>
        <label class="toggle"><input type="checkbox" id="save">
          <span class="track"></span><span class="knob"></span></label>
      </div>
      <div class="row" id="keep-row">
        <div class="body"><div class="label" data-i18n="keep.label">Keep last</div>
          <div class="help" data-i18n="keep.help">Older recordings are deleted automatically</div></div>
        <select id="keep"></select>
      </div>
      <div class="row">
        <div class="body"><div class="label" data-i18n="folder.label">Storage folder</div>
          <div class="help" id="rec-path"></div></div>
        <button id="open-folder" data-i18n="folder.open">Open</button>
      </div>
    </section>

    <div class="sec-title" data-i18n="sec.dictation">Dictation</div>
    <section>
      <div class="row">
        <div class="body"><div class="label" data-i18n="dict.mode">Dictation mode</div>
          <div class="help" style="white-space:normal" data-i18n="dict.modeHelp">Batch transcribes the whole clip at the end; streaming types each sentence as you speak</div></div>
        <select id="dictmode"></select>
      </div>
      <div class="row nav" id="go-profiles">
        <div class="body"><div class="label" data-i18n="profiles.nav">Speech profiles</div>
          <div class="help" id="prof-sub">Prompt-priming for better recognition</div></div>
        <span class="chev">›</span>
      </div>
      <div class="row nav" id="go-hotkeys">
        <div class="body"><div class="label" data-i18n="hotkeys.nav">Hotkeys</div>
          <div class="help" data-i18n="hotkeys.navHelp">Dictation, language and profile sets</div></div>
        <span class="chev">›</span>
      </div>
    </section>

    <div class="sec-title" data-i18n="sec.system">System</div>
    <section>
      <div class="row">
        <div class="body"><div class="label" data-i18n="theme.label">Appearance</div>
          <div class="help" data-i18n="theme.help">Window theme — follow macOS, or force light / dark</div></div>
        <select id="theme"></select>
      </div>
      <div class="row">
        <div class="body"><div class="label" data-i18n="applang.label">App language</div>
          <div class="help" data-i18n="applang.help">Language of the copied AI prompt</div></div>
        <select id="lang"></select>
      </div>
      <div class="row">
        <div class="body"><div class="label" data-i18n="login.label">Launch at login</div>
          <div class="help" data-i18n="login.help">Start Cream Typer automatically when you log in</div></div>
        <label class="toggle"><input type="checkbox" id="login">
          <span class="track"></span><span class="knob"></span></label>
      </div>
    </section>
  </div>

  <!-- ── Profiles screen (drill-in) ───────────────────────────────────────── -->
  <div id="screen-profiles" class="screen">
    <header>
      <span class="back" id="back">
        <svg viewBox="0 0 12 12" fill="none"><path d="M7.5 1.5L3 6l4.5 4.5"
          stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
          stroke-linejoin="round"/></svg><span data-i18n="back">Settings</span></span>
      <h1 style="flex:0 1 auto" data-i18n="pscreen.title">Speech profiles</h1>
      <span style="flex:1"></span>
    </header>
    <div class="ai" data-i18n="pscreen.ai">A profile is one natural sentence that primes whisper toward your
      real vocabulary — names, jargon, English terms inside Ukrainian. Toggle the ones
      that fit what you're dictating; the meter shows how much of whisper's prompt
      budget each language uses.</div>
    <div class="notice" id="notice"></div>
    <div id="groups"></div>

    <div class="sec-title" data-i18n="psec.sets">Profile sets</div>
    <div class="ai" data-i18n="sets.help">Press ⌃⌥‹digit› to switch a whole set of profiles on at once.</div>
    <div id="sets"></div>

    <div class="sec-title" data-i18n="psec.ai">Build profiles with your AI</div>
    <section>
      <div class="row">
        <div class="body"><div class="label" data-i18n="copyai.label">Copy AI prompt</div>
          <div class="help" style="white-space:normal" data-i18n="copyai.help">Copies a ready prompt. Paste it into
            ChatGPT / Gemini / Claude — it knows you from your chats and writes profiles as
            JSON, which you then import below.</div></div>
        <button id="copy-ai" data-i18n="copyai.btn">Copy</button>
      </div>
      <div class="row">
        <div class="body"><div class="label" data-i18n="import.label">Import profiles</div>
          <div class="help" data-i18n="import.help">Paste the JSON your AI returned</div></div>
        <button id="import-toggle" data-i18n="import.btn">Import…</button>
      </div>
      <div id="import-panel" hidden>
        <div class="pform" style="margin:0 0 8px">
          <label data-i18n="import.paste">Paste JSON here</label>
          <textarea id="import-text" placeholder='[{"name":"…","language":"uk","prompt":"…"}]'></textarea>
          <div class="frow">
            <button class="ghost" id="import-cancel" data-i18n="import.cancel">Cancel</button>
            <button class="primary" id="import-do" data-i18n="import.do">Import</button>
          </div>
        </div>
      </div>
    </section>
  </div>

  <!-- ── Hotkeys screen (drill-in) ────────────────────────────────────────── -->
  <div id="screen-hotkeys" class="screen">
    <header>
      <span class="back" id="back-hk">
        <svg viewBox="0 0 12 12" fill="none"><path d="M7.5 1.5L3 6l4.5 4.5"
          stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
          stroke-linejoin="round"/></svg><span data-i18n="back">Settings</span></span>
      <h1 style="flex:0 1 auto" data-i18n="hkscreen.title">Hotkeys</h1>
      <span style="flex:1"></span>
    </header>
    <section>
      <div class="row">
        <div class="body"><div class="label" data-i18n="hotkey.label">Dictation hotkey</div>
          <div class="help" data-i18n="hotkey.help">Tap to start, tap again to stop</div></div>
        <div class="hkcap"><kbd id="hk-toggle"></kbd>
          <button class="iconbtn ghost" id="hk-toggle-btn" data-i18n="hotkey.change">Change…</button></div>
      </div>
      <div class="row" style="display:block">
        <div class="label" data-i18n="langhk.label">Language shortcuts</div>
        <div class="help" style="white-space:normal" data-i18n="langhk.help">Switch the decode language
          anywhere, without opening the menu. Use any key with ⌃⌥⌘⇧ held, or a key
          that types nothing on its own (Caps Lock, a modifier, an F-key).</div>
        <div class="kslist" id="lang-hotkeys"></div>
      </div>
    </section>

    <div class="sec-title" data-i18n="psec.sethk">Profile-set shortcuts</div>
    <div class="ai" data-i18n="setshk.help">Each set is switched on with ⌃⌥‹digit›. Manage the sets in Speech profiles.</div>
    <div id="set-shortcuts"></div>
  </div>

<script>
let STATE = /*__STATE__*/null;
let view = "main";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
};
function send(action, value){
  const msg = (value === undefined) ? {action} : {action, value};
  window.webkit.messageHandlers.creamBridge.postMessage(JSON.stringify(msg));
}
function applyAccent(){
  if (STATE.accent) document.documentElement.style.setProperty("--accent", STATE.accent);
}

// ── i18n ────────────────────────────────────────────────────────────────────
// STATE.t is the localized string table (see i18n.py). T() looks a key up with a
// fallback; applyI18n() fills every static [data-i18n] element from it.
function T(key, fallback){
  const v = STATE.t && STATE.t[key];
  return (v === undefined || v === null) ? (fallback !== undefined ? fallback : key) : v;
}
function applyI18n(){
  document.querySelectorAll("[data-i18n]").forEach(n => {
    const v = STATE.t && STATE.t[n.getAttribute("data-i18n")];
    if (v !== undefined && v !== null) n.textContent = v;
  });
  // Select options are built once but their labels are localized — relabel by
  // value so a live language switch updates them too.
  const keep = $("keep");
  if (keep) [...keep.options].forEach(o => {
    o.textContent = o.value + " " + T("keep.unit", "recordings");
  });
  const theme = $("theme"), TH = {auto:"theme.auto", light:"theme.light", dark:"theme.dark"};
  if (theme) [...theme.options].forEach(o => { o.textContent = T(TH[o.value]); });
  const dm = $("dictmode"), DM = {batch:"dict.batch", streaming:"dict.streaming"};
  if (dm) [...dm.options].forEach(o => { o.textContent = T(DM[o.value]); });
}

// ── Screen navigation ──────────────────────────────────────────────────────
function show(name){
  view = name;
  $("screen-main").classList.toggle("on", name === "main");
  $("screen-profiles").classList.toggle("on", name === "profiles");
  $("screen-hotkeys").classList.toggle("on", name === "hotkeys");
  window.scrollTo(0, 0);
}
$("go-profiles").addEventListener("click", () => show("profiles"));
$("go-hotkeys").addEventListener("click", () => show("hotkeys"));
$("back").addEventListener("click", () => show("main"));
$("back-hk").addEventListener("click", () => show("main"));

// ── Static general controls (built once) ───────────────────────────────────
(function(){
  const sel = $("mic");
  const mk = (label, val, cur) => {
    const o = document.createElement("option");
    o.textContent = label; o.value = val;
    if (val === cur) o.selected = true;
    return o;
  };
  const cur = STATE.current_mic || "";
  sel.appendChild(mk("Default", "", cur));
  (STATE.mics || []).forEach(name => sel.appendChild(mk(name, name, cur)));
  sel.addEventListener("change", () => send("set_mic", sel.value || null));

  const keep = $("keep");
  (STATE.keep_last_options || [5,10,20]).forEach(n => {
    const o = document.createElement("option");
    o.textContent = n + " " + T("keep.unit", "recordings"); o.value = String(n);
    if (n === STATE.keep_last) o.selected = true;
    keep.appendChild(o);
  });
  keep.addEventListener("change", () => send("set_keep", parseInt(keep.value, 10)));

  const save = $("save"), keepRow = $("keep-row");
  const sync = () => keepRow.classList.toggle("disabled", !save.checked);
  save.checked = !!STATE.save_recordings; sync();
  save.addEventListener("change", () => { sync(); send("set_save", save.checked); });

  const login = $("login");
  login.checked = !!STATE.launch_at_login;
  login.addEventListener("change", () => send("set_login", login.checked));

  const theme = $("theme");
  [["auto",T("theme.auto","Automatic")],["light",T("theme.light","Light")],
   ["dark",T("theme.dark","Dark")]].forEach(([val,label]) => {
    const o = document.createElement("option");
    o.value = val; o.textContent = label;
    if (val === (STATE.ui_theme || "auto")) o.selected = true;
    theme.appendChild(o);
  });
  theme.addEventListener("change", () => send("set_theme", theme.value));

  const lang = $("lang");
  [["uk","Українська"],["en","English"]].forEach(([val,label]) => {
    const o = document.createElement("option");
    o.value = val; o.textContent = label;
    if (val === (STATE.ui_lang || "uk")) o.selected = true;
    lang.appendChild(o);
  });
  lang.addEventListener("change", () => send("set_lang", lang.value));

  const dictmode = $("dictmode");
  [["batch",T("dict.batch","Batch")],["streaming",T("dict.streaming","Streaming")]]
    .forEach(([val,label]) => {
    const o = document.createElement("option");
    o.value = val; o.textContent = label;
    if (val === (STATE.dictation_mode || "batch")) o.selected = true;
    dictmode.appendChild(o);
  });
  dictmode.addEventListener("change", () => send("set_dictation_mode", dictmode.value));

  $("rec-path").textContent = STATE.recordings_dir || "";
  $("open-folder").addEventListener("click", () => send("open_folder"));

  // Capture the dictation toggle: ask Python to record the next keypress. The
  // kbd label and the language rows are refreshed by renderHotkeys() once the
  // new binding lands (state push). The button is built once → wire it here.
  $("hk-toggle-btn").addEventListener("click", () => {
    $("hk-toggle").textContent = T("hotkey.press", "Press keys…");
    send("capture_hotkey", "__toggle__");
  });

  $("copy-ai").addEventListener("click", () => {
    // Carry the picked language so the copy never depends on set_lang round-tripping.
    send("copy_ai_prompt", $("lang").value || STATE.ui_lang || "uk");
    flash(T("copyai.done", "Prompt copied to clipboard."));
  });

  const panel = $("import-panel"), ta = $("import-text");
  $("import-toggle").addEventListener("click", () => {
    panel.hidden = !panel.hidden; if (!panel.hidden) ta.focus();
  });
  $("import-cancel").addEventListener("click", () => { panel.hidden = true; ta.value = ""; });
  $("import-do").addEventListener("click", () => {
    const t = ta.value.trim(); if (!t) return;
    send("import_profiles", t); ta.value = ""; panel.hidden = true;
  });
})();

// ── Profiles (re-rendered on every state push) ─────────────────────────────
const LANGS = {uk:"Українська", en:"English", ru:"Русский", es:"Español",
  de:"Deutsch", fr:"Français", it:"Italiano", pt:"Português", nl:"Nederlands",
  pl:"Polski", ja:"日本語", zh:"中文", ko:"한국어", tr:"Türkçe", th:"ไทย",
  vi:"Tiếng Việt", ar:"العربية"};
const ALL_LANGS = Object.keys(LANGS);  // editor offers every language we decode
const est = (t) => t ? Math.max(1, Math.round(t.length / 3)) : 0;  // ~ profiles.estimate_tokens
const langLabel = (c) => LANGS[c] || c.toUpperCase();

let flashTimer = null;
function flash(msg){
  const n = $("notice"); n.textContent = msg;
  clearTimeout(flashTimer); flashTimer = setTimeout(() => { n.textContent = ""; }, 3500);
}

function renderProfiles(){
  const root = $("groups"); root.innerHTML = "";
  const profiles = STATE.profiles || [];
  const active = STATE.active_profiles || {};
  const isOn = (lang, name) => (active[lang] || []).includes(name);
  const BUDGET = STATE.token_budget || 224;

  const langs = [...new Set(profiles.map(p => p.language || "uk"))];
  const cur = STATE.current_lang;
  langs.sort((a, b) => (a === cur ? -1 : b === cur ? 1 : a.localeCompare(b)));
  if (langs.length === 0) langs.push(cur || "uk");
  const langOptions = ALL_LANGS;  // create a profile in any language → its group appears

  $("prof-sub").textContent = T("prof.count", "Profiles: {n}").replace("{n}", profiles.length);

  langs.forEach(lang => {
    const g = el("div", "pgroup");
    const head = el("div", "plang");
    head.appendChild(el("span", "name", langLabel(lang)));
    const meter = el("div", "meter"); const bar = el("i"); meter.appendChild(bar);
    head.appendChild(meter);
    const count = el("span", "count"); head.appendChild(count);
    g.appendChild(head);

    const sec = el("section"); sec.style.marginBottom = "4px";
    const rows = profiles.filter(p => (p.language || "uk") === lang);

    const refreshMeter = () => {
      let used = 0;
      rows.forEach(p => { if (isOn(lang, p.name)) used += est(p.prompt || ""); });
      bar.style.width = Math.min(100, used / BUDGET * 100) + "%";
      meter.classList.toggle("over", used > BUDGET);
      count.textContent = used + "/" + BUDGET;
    };

    rows.forEach(p => {
      const row = el("div", "prow");
      const tog = el("label", "toggle");
      const cb = el("input"); cb.type = "checkbox"; cb.checked = isOn(lang, p.name);
      tog.appendChild(cb); tog.appendChild(el("span", "track")); tog.appendChild(el("span", "knob"));
      const name = el("span", "pname" + (cb.checked ? "" : " off"), p.name);
      cb.addEventListener("change", () => {
        const arr = active[lang] || (active[lang] = []);
        if (cb.checked && !arr.includes(p.name)) arr.push(p.name);
        if (!cb.checked) { const i = arr.indexOf(p.name); if (i >= 0) arr.splice(i, 1); }
        name.classList.toggle("off", !cb.checked);
        refreshMeter();
        send("toggle_profile", {name: p.name, active: cb.checked});
      });
      const edit = el("button", "iconbtn ghost", T("prow.edit", "Edit"));
      edit.addEventListener("click", () => openForm(sec, row, lang, langOptions, p));
      // Two-step delete: WKWebView blocks window.confirm(), so confirm inline.
      const del = el("button", "iconbtn danger", T("prow.delete", "Delete"));
      let armed = false, armTimer = null;
      del.addEventListener("click", () => {
        if (!armed) {
          armed = true; del.textContent = T("prow.confirm", "Confirm?");
          armTimer = setTimeout(() => { armed = false; del.textContent = T("prow.delete", "Delete"); }, 2500);
          return;
        }
        clearTimeout(armTimer);
        send("delete_profile", p.name);  // Python persists, then pushes fresh state
      });
      row.appendChild(tog); row.appendChild(name); row.appendChild(edit); row.appendChild(del);
      sec.appendChild(row);
    });

    g.appendChild(sec);
    const add = el("button", "addbtn ghost", T("prow.add", "+ Add profile"));
    add.addEventListener("click", () => openForm(sec, null, lang, langOptions, null));
    g.appendChild(add);
    root.appendChild(g);
    refreshMeter();
  });
}

// Inline add/edit form. `profile` null → create; else edit (original name lets a
// rename happen server-side). Inserted after `anchorRow` (edit) or at end (add).
function openForm(sec, anchorRow, lang, langOptions, profile){
  if (sec.parentElement.querySelector(".pform")) return;  // one open form per group
  const f = el("div", "pform");

  f.appendChild(el("label", null, T("form.name", "Name")));
  const name = el("input"); name.type = "text"; name.value = profile ? profile.name : "";
  name.placeholder = T("form.namePh", "e.g. Розробка / код"); f.appendChild(name);

  f.appendChild(el("label", null, T("form.lang", "Language")));
  const langSel = document.createElement("select");
  langOptions.forEach(code => {
    const o = document.createElement("option");
    o.value = code; o.textContent = langLabel(code);
    if (code === (profile ? (profile.language || lang) : lang)) o.selected = true;
    langSel.appendChild(o);
  });
  f.appendChild(langSel);

  f.appendChild(el("label", null, T("form.prompt", "Prompt — one natural sentence that primes recognition")));
  const ta = document.createElement("textarea");
  ta.value = profile ? (profile.prompt || "") : ""; f.appendChild(ta);

  const frow = el("div", "frow");
  const estLbl = el("span", "est");
  const upd = () => { estLbl.textContent = "≈ " + est(ta.value) + " " + T("form.tokens", "tokens"); };
  ta.addEventListener("input", upd); upd();
  const cancel = el("button", "ghost", T("form.cancel", "Cancel"));
  cancel.addEventListener("click", () => f.remove());
  const save = el("button", "primary", T("form.save", "Save"));
  save.addEventListener("click", () => {
    const payload = {
      name: name.value.trim(), language: langSel.value,
      prompt: ta.value.trim(), original: profile ? profile.name : "",
    };
    if (!payload.name || !payload.prompt) { name.focus(); return; }
    send("save_profile", payload);  // Python persists then pushes fresh state
  });
  frow.appendChild(estLbl); frow.appendChild(cancel); frow.appendChild(save);
  f.appendChild(frow);

  if (anchorRow) anchorRow.after(f); else sec.appendChild(f);
  name.focus();
}

// ── Profile sets (re-rendered on every state push) ─────────────────────────
// A set is a named bundle of profiles activated all-at-once by ⌃⌥<digit> (the
// digit = its 1-based position). The badge label comes from Python (set.label).
function renderSets(){
  const root = $("sets"); if (!root) return;
  root.innerHTML = "";
  const sets = STATE.profile_sets || [];
  const max = STATE.max_sets || 9;
  const sec = el("section"); sec.style.marginBottom = "4px";

  if (sets.length === 0){
    const none = el("div", "srow");
    none.appendChild(el("span", "smeta", T("sets.none", "No sets yet.")));
    sec.appendChild(none);
  }

  sets.forEach((s, i) => {
    const row = el("div", "srow" + (s.active ? " on" : ""));
    row.appendChild(el("kbd", s.active ? "on" : "", s.label || ""));
    const body = el("div", "sbody");
    body.appendChild(el("div", "sname", s.name));
    body.appendChild(el("div", "smeta", (s.members || []).join(", ") || T("set.empty", "(empty set)")));
    row.appendChild(body);

    // Explicit live state: an "Active" pill when this set IS the current
    // selection (Python computes it), else an Activate button that switches to it.
    if (s.active) {
      row.appendChild(el("span", "spill", T("set.active", "Active")));
    } else {
      const act = el("button", "iconbtn ghost", T("set.activate", "Activate"));
      act.addEventListener("click", () => send("activate_set", i));  // state push marks it active
      row.appendChild(act);
    }
    const edit = el("button", "iconbtn ghost", T("prow.edit", "Edit"));
    edit.addEventListener("click", () => openSetForm(sec, row, s, i));
    const del = el("button", "iconbtn danger", T("prow.delete", "Delete"));
    let armed = false, armTimer = null;
    del.addEventListener("click", () => {
      if (!armed) {
        armed = true; del.textContent = T("prow.confirm", "Confirm?");
        armTimer = setTimeout(() => { armed = false; del.textContent = T("prow.delete", "Delete"); }, 2500);
        return;
      }
      clearTimeout(armTimer);
      send("delete_set", i);  // Python persists + re-binds, then pushes fresh state
    });
    row.appendChild(edit); row.appendChild(del);
    sec.appendChild(row);
  });

  root.appendChild(sec);
  if (sets.length < max) {
    const add = el("button", "addbtn ghost", T("set.add", "+ Add set"));
    add.addEventListener("click", () => openSetForm(sec, null, null, null));
    root.appendChild(add);
  }
}

// Inline add/edit form for a set: name + a checkbox list of every profile.
function openSetForm(sec, anchorRow, set, index){
  if (sec.parentElement.querySelector(".pform")) return;  // one open form at a time
  const f = el("div", "pform");

  f.appendChild(el("label", null, T("form.name", "Name")));
  const name = el("input"); name.type = "text"; name.value = set ? set.name : "";
  name.placeholder = T("set.namePh", "e.g. Coding"); f.appendChild(name);

  f.appendChild(el("label", null, T("set.members", "Profiles in set")));
  const list = el("div", "setmembers");
  const profiles = STATE.profiles || [];
  const chosen = new Set(set ? (set.members || []) : []);
  const boxes = [];
  if (profiles.length === 0) list.appendChild(el("div", "none", "—"));
  profiles.forEach(p => {
    const lab = el("label");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = chosen.has(p.name); cb.value = p.name;
    cb.addEventListener("change", updateMeters);
    lab.appendChild(cb);
    lab.appendChild(el("span", null, p.name));
    lab.appendChild(el("span", "mlang", langLabel(p.language || "uk")));
    list.appendChild(lab); boxes.push(cb);
  });
  f.appendChild(list);

  // Token budget per language — whisper composes one prompt per decode language,
  // so the budget is per-language. Shows the user when a selection overflows
  // (the meter turns red), which they couldn't otherwise see while ticking boxes.
  const BUDGET = STATE.token_budget || 224;
  const meterbox = el("div"); meterbox.style.margin = "0 0 11px";
  f.appendChild(meterbox);
  function updateMeters(){
    meterbox.innerHTML = "";
    const byLang = {};
    boxes.forEach(b => {
      if (!b.checked) return;
      const p = profiles.find(x => x.name === b.value); if (!p) return;
      const l = p.language || "uk";
      byLang[l] = (byLang[l] || 0) + est(p.prompt || "");
    });
    Object.keys(byLang).sort().forEach(l => {
      const used = byLang[l], over = used > BUDGET;
      const head = el("div", "plang");
      head.appendChild(el("span", "name", langLabel(l)));
      const meter = el("div", "meter" + (over ? " over" : "")); const bar = el("i");
      bar.style.width = Math.min(100, used / BUDGET * 100) + "%"; meter.appendChild(bar);
      head.appendChild(meter);
      head.appendChild(el("span", "count", used + "/" + BUDGET));
      meterbox.appendChild(head);
    });
  }
  updateMeters();

  const frow = el("div", "frow");
  const cancel = el("button", "ghost", T("form.cancel", "Cancel"));
  cancel.addEventListener("click", () => f.remove());
  const save = el("button", "primary", T("form.save", "Save"));
  save.addEventListener("click", () => {
    const nm = name.value.trim(); if (!nm) { name.focus(); return; }
    const members = boxes.filter(b => b.checked).map(b => b.value);
    send("save_set", {index: (index === null || index === undefined) ? null : index, name: nm, members});
  });
  frow.appendChild(cancel); frow.appendChild(save);
  f.appendChild(frow);

  if (anchorRow) anchorRow.after(f); else sec.appendChild(f);
  name.focus();
}

// Set shortcuts on the Hotkeys screen — discoverable alongside the others and
// reassignable here. The set's membership is edited on the Profiles screen.
function renderSetShortcuts(){
  const root = $("set-shortcuts"); if (!root) return;
  root.innerHTML = "";
  const sets = STATE.profile_sets || [];
  if (sets.length === 0){
    root.appendChild(el("div", "ai", T("sets.none", "No sets yet.")));
    return;
  }
  const wrap = el("div", "kslist");
  sets.forEach((s, i) => {
    const row = el("div", "ksrow");
    row.appendChild(el("kbd", s.active ? "on" : "", s.label || ""));
    row.appendChild(el("span", "kslabel", s.name));
    // Reassignable: a set always has a working shortcut (default ⌃⌥<digit>),
    // and can be rebound to any key. "assigned" means a custom override is set.
    const btn = el("button", "iconbtn ghost", T("langhk.change", "Change…"));
    btn.addEventListener("click", () => {
      btn.textContent = T("hotkey.press", "Press keys…");
      send("capture_hotkey", "set:" + i);
    });
    row.appendChild(btn);
    if (s.assigned) {
      const clr = el("button", "iconbtn ghost clearbtn", "✕");
      clr.title = T("sethk.reset", "Reset to default");
      clr.addEventListener("click", () => send("clear_hotkey", "set:" + i));
      row.appendChild(clr);
    }
    wrap.appendChild(row);
  });
  root.appendChild(wrap);
}

// ── Hotkeys (re-rendered on every state push) ──────────────────────────────
function renderHotkeys(){
  const tog = $("hk-toggle");
  if (tog) tog.textContent = STATE.hotkey_label || "Caps Lock";
  const wrap = $("lang-hotkeys");
  if (!wrap) return;
  wrap.innerHTML = "";
  (STATE.lang_hotkeys || []).forEach(h => {
    const row = el("div", "ksrow");
    const kb = el("kbd", h.assigned ? "" : "muted", h.assigned ? h.label : "—");
    row.appendChild(kb);
    row.appendChild(el("span", "kslabel", h.lang_label));
    const btn = el("button", "iconbtn ghost",
      h.assigned ? T("langhk.change", "Change…") : T("langhk.assign", "Assign…"));
    btn.addEventListener("click", () => {
      btn.textContent = T("hotkey.press", "Press keys…");
      send("capture_hotkey", h.action);
    });
    row.appendChild(btn);
    if (h.assigned) {
      const clr = el("button", "iconbtn ghost clearbtn", "✕");
      clr.title = T("langhk.remove", "Remove shortcut");
      clr.addEventListener("click", () => send("clear_hotkey", h.action));
      row.appendChild(clr);
    }
    wrap.appendChild(row);
  });
}

// ── State push from Python (no reload → keeps the current screen) ──────────
window.creamApply = function(s){
  STATE = s;
  applyAccent();
  applyI18n();
  renderProfiles();
  renderSets();
  renderSetShortcuts();
  renderHotkeys();
  if (s.notice) flash(s.notice);
};

applyAccent();
applyI18n();
renderProfiles();
renderSets();
renderHotkeys();
</script>
</body>
</html>"""


# ── AppKit/WebKit glue (runtime only) ────────────────────────────────────────


def _apply_dock_icon() -> None:
    """Set the running app's Dock icon to the Cream Typer mark. Must be called
    *after* switching to a Regular activation policy (the tile is recreated then,
    discarding any icon set earlier while accessory)."""
    from pathlib import Path

    try:
        from AppKit import NSApplication, NSImage

        icns = Path(__file__).resolve().parents[2] / "assets" / "CreamTyper.icns"
        img = NSImage.alloc().initWithContentsOfFile_(str(icns)) if icns.exists() else None
        if img is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception as e:
        print(f"⚠️ could not set Dock icon: {e}")


def _install_main_menu() -> None:
    """Give the app a real menu bar while a window is open.

    A menu-bar agent has no main menu, so when we flip to Regular and show the
    Settings window the *previous* app's menu bar stays drawn at the top — looks
    like a hang. Installing a minimal App + Edit menu replaces it; the Edit menu
    also wires Cmd+C/V/X/A/Z into the text fields (import paste, profile prompt),
    which a WKWebView only gets from the responder chain via these selectors."""
    try:
        from AppKit import NSApp, NSMenu, NSMenuItem

        app = NSApp()
        main = NSMenu.alloc().init()

        app_item = NSMenuItem.alloc().init()
        main.addItem_(app_item)
        app_menu = NSMenu.alloc().init()
        app_item.setSubmenu_(app_menu)
        app_menu.addItemWithTitle_action_keyEquivalent_("Hide Cream Typer", "hide:", "h")
        app_menu.addItem_(NSMenuItem.separatorItem())
        app_menu.addItemWithTitle_action_keyEquivalent_("Quit Cream Typer", "terminate:", "q")

        edit_item = NSMenuItem.alloc().init()
        main.addItem_(edit_item)
        edit_menu = NSMenu.alloc().initWithTitle_("Edit")
        edit_item.setSubmenu_(edit_menu)
        for title, sel, key in (
            ("Undo", "undo:", "z"),
            ("Redo", "redo:", "Z"),
            (None, None, None),
            ("Cut", "cut:", "x"),
            ("Copy", "copy:", "c"),
            ("Paste", "paste:", "v"),
            ("Select All", "selectAll:", "a"),
        ):
            if title is None:
                edit_menu.addItem_(NSMenuItem.separatorItem())
            else:
                edit_menu.addItemWithTitle_action_keyEquivalent_(title, sel, key)

        app.setMainMenu_(main)
    except Exception as e:
        print(f"⚠️ could not install main menu: {e}")


def _accent_hex() -> str | None:
    """The user's current macOS accent as an sRGB hex string, or None on failure.

    Resolved at runtime (not cached) so a mid-session accent change is picked up
    the next time the window opens. Cosmetic — any error just falls back to the
    stylesheet default.
    """
    try:
        from AppKit import NSColor, NSColorSpace

        c = NSColor.controlAccentColor().colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
        r, g, b = (
            round(c.redComponent() * 255),
            round(c.greenComponent() * 255),
            round(c.blueComponent() * 255),
        )
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return None


class SettingsWindow:
    """Lazily-built NSWindow + WKWebView. Reused across opens.

    state_provider() returns a fresh state dict; handlers maps action names (see
    build_html's send() calls) to callables invoked on the main thread.

    The page is loaded once and thereafter updated in place via refresh(), so a
    profile edit/delete/import keeps the user on the Profiles screen.

    All AppKit/WebKit objects are held on `self` so PyObjC doesn't deallocate
    them out from under the running window.
    """

    _WIDTH = 460
    _HEIGHT = 680
    _MIN_W = 420
    _MIN_H = 460

    def __init__(self, state_provider: Callable[[], dict], handlers: dict[str, Callable]):
        self._state_provider = state_provider
        self._handlers = handlers
        self._window = None
        self._webview = None
        self._bridge = None
        self._loaded = False

    def _state(self) -> dict:
        state = self._state_provider()
        accent = _accent_hex()
        if accent:
            state["accent"] = accent
        return state

    def show(self) -> None:
        """Build (first call) or re-show the window. Must run on the main thread."""
        first = self._window is None
        if first:
            self._build()
            self._webview.loadHTMLString_baseURL_(build_html(self._state()), None)
            self._loaded = True
        else:
            self.refresh()

        from AppKit import NSApp

        # While the window is visible, become a Regular app so it gets a Dock tile,
        # a Cmd-Tab entry and a Stage Manager thumbnail. Reverts on close.
        NSApp().setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
        _install_main_menu()  # replace the stale menu bar of the previously-active app
        _apply_dock_icon()
        NSApp().activateIgnoringOtherApps_(True)
        if first:
            self._window.center()
        with contextlib.suppress(Exception):
            self.apply_theme(self._state_provider().get("ui_theme", "auto"))
        self._window.makeKeyAndOrderFront_(None)

    def apply_theme(self, theme: str) -> None:
        """Force the window (and its WKWebView, which inherits the appearance →
        CSS prefers-color-scheme) to light/dark, or follow macOS when 'auto'."""
        if self._window is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import NSAppearance

            name = {"light": "NSAppearanceNameAqua", "dark": "NSAppearanceNameDarkAqua"}.get(theme)
            ap = NSAppearance.appearanceNamed_(name) if name else None
            self._window.setAppearance_(ap)

    def refresh(self, notice: str | None = None) -> None:
        """Push fresh state into the open page (after an edit/delete/import), so
        the front-end re-renders without a reload — the current screen is kept."""
        if not self._loaded or self._webview is None:
            return
        state = self._state()
        if notice:
            state["notice"] = notice
        js = f"window.creamApply({_encode(state)});"
        with contextlib.suppress(Exception):
            self._webview.evaluateJavaScript_completionHandler_(js, None)

    def _build(self) -> None:
        from AppKit import (
            NSBackingStoreBuffered,
            NSMakeRect,
            NSMakeSize,
            NSWindow,
            NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable,
            NSWindowStyleMaskResizable,
            NSWindowStyleMaskTitled,
        )
        from WebKit import WKWebView, WKWebViewConfiguration

        self._bridge = _Bridge.alloc().initWithOwner_(self)

        # Live-update the accent: macOS posts this distributed notification when the
        # user changes the accent/highlight colour. Delivered on the main run loop
        # (where we register), so refresh() — which re-reads controlAccentColor — is
        # safe to call straight from the handler. No need to reopen the window.
        with contextlib.suppress(Exception):
            from Foundation import NSDistributedNotificationCenter

            NSDistributedNotificationCenter.defaultCenter().addObserver_selector_name_object_(
                self._bridge, "accentDidChange:", "AppleColorPreferencesChangedNotification", None
            )

        config = WKWebViewConfiguration.alloc().init()
        config.userContentController().addScriptMessageHandler_name_(self._bridge, "creamBridge")

        frame = NSMakeRect(0, 0, self._WIDTH, self._HEIGHT)
        self._webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
        with contextlib.suppress(Exception):
            self._webview.setValue_forKey_(False, "drawsBackground")

        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False
        )
        win.setTitle_("Cream Typer — Settings")
        win.setContentView_(self._webview)
        win.setReleasedWhenClosed_(False)  # we reuse it; don't let AppKit free it
        win.setDelegate_(self._bridge)
        with contextlib.suppress(Exception):
            win.setMinSize_(NSMakeSize(self._MIN_W, self._MIN_H))
        self._window = win


def _make_bridge_class():
    """Defines the Obj-C bridge lazily, so importing this module needs no AppKit.

    One object wears two hats: WKScriptMessageHandler (JS→Python) and
    NSWindowDelegate (reset activation policy on close)."""
    from AppKit import NSObject

    class _BridgeImpl(NSObject):
        def initWithOwner_(self, owner):
            self = objc_super_init(self)
            if self is None:
                return None
            self._owner = owner
            return self

        # WKScriptMessageHandler
        def userContentController_didReceiveScriptMessage_(self, controller, message):
            try:
                dispatch(json.loads(message.body()), self._owner._handlers)
            except Exception as e:  # never let a bad message crash AppKit
                print(f"⚠️ settings bridge: {e}")

        # System accent/highlight colour changed. controlAccentColor still returns
        # the OLD value at the instant the notification fires (AppKit updates it a
        # beat later), so re-read after a short delay rather than immediately.
        def accentDidChange_(self, note):
            with contextlib.suppress(Exception):
                self.performSelector_withObject_afterDelay_("doRefresh:", None, 0.18)

        def doRefresh_(self, _arg):
            with contextlib.suppress(Exception):
                self._owner.refresh()

        # Belt-and-braces: re-read the accent whenever the window regains focus
        # (e.g. the user clicks back from System Settings after changing the
        # colour), so it's correct even if the distributed notification is missed.
        def windowDidBecomeKey_(self, notification):
            with contextlib.suppress(Exception):
                self._owner.refresh()

        # NSWindowDelegate — drop back to an accessory app (no Dock tile) once the
        # settings window closes, restoring the menu-bar-only footprint.
        def windowWillClose_(self, notification):
            with contextlib.suppress(Exception):
                from AppKit import NSApp

                NSApp().setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    return _BridgeImpl


def objc_super_init(obj):
    # objc.super(...).init() equivalent without importing objc at module top.
    import objc

    return objc.super(obj.__class__, obj).init()


# Built on first access (keeps module import AppKit-free for tests).
class _BridgeMeta:
    _cls = None

    def alloc(self):
        if _BridgeMeta._cls is None:
            _BridgeMeta._cls = _make_bridge_class()
        return _BridgeMeta._cls.alloc()


_Bridge = _BridgeMeta()
