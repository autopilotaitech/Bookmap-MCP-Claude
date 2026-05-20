"""Phase 0a spike v2 — production-aesthetic Pax AI window preview.

Goals (per feedback v1 was "dog shit, not modern dark glass"):
  * Compact: 360x500 default (not 380x680).
  * Premium dark UI in the Linear/Arc/Vercel aesthetic — opaque solid surface,
    subtle inner gradients + accent rim + drop shadow. True desktop
    transparency is unreliable on Edge WebView2 / DWM on Win11 and worse UX
    for chat over a busy chart (legibility wins over gimmick).
  * Show a REAL product preview: context strip + 2 sample chat turns +
    proactive WHY-NOW chip + input row with mic icon + send. The point is
    to commit to a visual language before scaffolding.
  * Frameless + on_top + draggable.

Run:
  C:\\Bookmap\\addons\\MCP\\Bookmap\\mcp-server\\.venv\\Scripts\\python.exe \\
    C:\\Bookmap\\addons\\MCP\\Bookmap\\pax-ai\\spikes\\spike_webview.py
"""

from __future__ import annotations

import sys

try:
    import webview
except ImportError:
    sys.stderr.write("pywebview not installed. Install with:\n"
                     "  mcp-server\\.venv\\Scripts\\pip install pywebview\n")
    sys.exit(2)


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Pax AI</title>
<style>
  :root {
    --bg-0: #07090d;
    --bg-1: #0b0f17;
    --bg-2: #11161f;
    --bg-3: #161c27;
    --line: rgba(255,255,255,0.045);
    --line-2: rgba(255,255,255,0.08);
    --text: #d8e3f0;
    --text-dim: #7c8a9c;
    --text-faint: #4d5868;
    --cyan: #36d6ff;
    --cyan-dim: #1a8fb3;
    --orange: #ff9b3c;
    --green: #4ade80;
    --red: #ff5a6a;
  }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; height:100%; width:100%;
    background: var(--bg-0); color: var(--text);
    font-family: 'Inter', 'Segoe UI Variable', 'Segoe UI', system-ui, sans-serif;
    font-size: 13px; line-height: 1.42;
    -webkit-user-select: none; user-select: none;
    -webkit-font-smoothing: antialiased;
  }
  .root {
    display: grid; grid-template-rows: 30px 36px 1fr 44px; height: 100vh;
    background:
      radial-gradient(120% 80% at 50% 0%, #131a25 0%, #0a0e15 55%, var(--bg-0) 100%);
    border: 1px solid rgba(54,214,255,0.10);
    box-shadow: 0 14px 50px rgba(0,0,0,0.55), 0 1px 0 rgba(255,255,255,0.04) inset;
  }
  /* —— title bar (drag) —— */
  .titlebar {
    -webkit-app-region: drag;
    display:flex; align-items:center; justify-content:space-between;
    padding: 0 8px 0 12px;
    border-bottom: 1px solid var(--line);
    background: linear-gradient(180deg, rgba(54,214,255,0.04), transparent);
  }
  .titlebar .brand { font-size: 11px; letter-spacing: 1.4px;
    color: var(--text-dim); font-weight: 600; }
  .titlebar .brand b { color: var(--cyan); font-weight: 700; }
  .titlebar .ctrls { -webkit-app-region: no-drag; display:flex; gap:6px; }
  .titlebar .dot { width:11px; height:11px; border-radius:50%;
    background: var(--bg-3); border: 1px solid var(--line-2); cursor:pointer; }
  .titlebar .dot:hover { filter: brightness(1.4); }
  .titlebar .dot.x { background:#3a1820; border-color: rgba(255,90,106,0.4); }
  .titlebar .dot.x:hover { background:#ff5a6a; }
  /* —— context strip —— */
  .strip {
    display:grid; grid-template-columns: auto 1fr auto;
    align-items:center; gap: 10px;
    padding: 0 12px;
    background: var(--bg-1);
    border-bottom: 1px solid var(--line);
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-size: 11.5px;
  }
  .strip .sym { color: var(--text); font-weight: 600; letter-spacing: 0.2px; }
  .strip .px { color: var(--cyan); font-variant-numeric: tabular-nums;
    font-weight: 600; }
  .strip .lvl { color: var(--text-dim); font-variant-numeric: tabular-nums; }
  .strip .lvl b { color: var(--text); font-weight: 600; }
  .strip .conv { color: var(--text-faint); font-size: 10.5px; }
  .strip .conv .pos { color: var(--green); }
  .strip .conv .neg { color: var(--red); }
  /* —— transcript area —— */
  .body { overflow-y: auto; padding: 10px 12px 6px;
    scrollbar-width: thin; scrollbar-color: #2a3242 transparent; }
  .body::-webkit-scrollbar { width: 6px; }
  .body::-webkit-scrollbar-thumb { background: #1f2735; border-radius: 3px; }
  .chip {
    display: flex; align-items: flex-start; gap: 8px;
    padding: 7px 9px 7px 10px;
    margin-bottom: 8px;
    background: linear-gradient(180deg, rgba(54,214,255,0.07), rgba(54,214,255,0.02));
    border: 1px solid rgba(54,214,255,0.22);
    border-radius: 7px;
    font-size: 11.5px; color: var(--text);
  }
  .chip .why { color: var(--cyan); font-weight: 700; letter-spacing: 0.6px;
    font-size: 10px; padding-top:1px; }
  .chip .msg { color: var(--text); }
  .chip .msg em { color: var(--text-dim); font-style: normal; }
  .turn { margin-bottom: 8px; }
  .you { color: var(--text-faint); font-size: 10.5px;
    text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 2px; }
  .you b { color: var(--text-dim); }
  .me { color: var(--text); font-size: 13px; }
  .pax .you b { color: var(--cyan); }
  .pax .me { color: var(--text); }
  .pax .me b { color: var(--cyan); }
  /* —— input —— */
  .input {
    display: grid; grid-template-columns: 1fr auto auto;
    align-items: center; gap: 8px;
    padding: 6px 10px 8px;
    border-top: 1px solid var(--line);
    background: var(--bg-1);
  }
  .input textarea {
    background: var(--bg-2);
    color: var(--text);
    border: 1px solid var(--line-2);
    border-radius: 8px;
    padding: 7px 10px;
    font: inherit;
    font-size: 13px;
    resize: none;
    height: 30px;
    line-height: 1.3;
    outline: none;
    transition: border-color .12s ease;
  }
  .input textarea::placeholder { color: var(--text-faint); }
  .input textarea:focus { border-color: rgba(54,214,255,0.45); }
  .icon-btn {
    width: 30px; height: 30px;
    background: var(--bg-2);
    border: 1px solid var(--line-2);
    border-radius: 8px;
    display:flex; align-items:center; justify-content:center;
    color: var(--text-dim); cursor: pointer;
    transition: all .12s ease;
  }
  .icon-btn:hover { color: var(--cyan); border-color: rgba(54,214,255,0.4); }
  .icon-btn.send { color: var(--cyan); border-color: rgba(54,214,255,0.3);
    background: linear-gradient(180deg, rgba(54,214,255,0.10), rgba(54,214,255,0.04)); }
  .icon-btn.send:hover { background: rgba(54,214,255,0.18); }
  svg { width: 14px; height: 14px; }
</style></head>
<body>
  <div class="root">
    <div class="titlebar">
      <div class="brand"><b>PAX</b> AI</div>
      <div class="ctrls">
        <div class="dot" title="minimize"></div>
        <div class="dot x" title="close" onclick="window.pywebview.api.close()"></div>
      </div>
    </div>

    <div class="strip">
      <div>
        <span class="sym">NQM6</span>&nbsp;
        <span class="px">21326.00</span>
      </div>
      <div class="lvl">+12.50p to <b>+1</b> &middot; FOLLOW&#9650;</div>
      <div class="conv">conv <span class="pos">+0.34</span> &middot; LIVE</div>
    </div>

    <div class="body">

      <div class="chip">
        <div class="why">WHY<br/>NOW</div>
        <div class="msg">Approaching <b>+1</b>, FOLLOW long, conf <b>0.62</b>.
          <em>STRONG_BULL trend just fired; VWAP bull aligned; no SPOOF on ask.</em></div>
      </div>

      <div class="turn">
        <div class="you"><b>YOU</b> &middot; 14:32:08</div>
        <div class="me">is +1 still in play?</div>
      </div>

      <div class="turn pax">
        <div class="you"><b>PAX</b> &middot; 14:32:09</div>
        <div class="me">Yes. STRONG_BULL holding into bucket 3 of 4, VWAP&nbsp;bias
          BULL, VP&nbsp;state ABOVE_VAH, no SPOOF on the offer the last 12s. Pay
          location at <b>+1</b>; trail to OR-H on confirmation.</div>
      </div>

      <div class="chip">
        <div class="why">WHY<br/>NOW</div>
        <div class="msg"><b>STOP_SWEEP</b> printed at <b>OR-L</b> 5s ago.
          <em>Watch for rotation; if the wash holds, FOLLOW long bias firms.</em></div>
      </div>

    </div>

    <div class="input">
      <textarea placeholder="ask about flow, levels, or hit&#160;&#127908;&#160;to speak&#8230;"
                rows="1"></textarea>
      <div class="icon-btn mic" title="voice (Web Speech API)">
        <svg viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 1.5a2 2 0 0 0-2 2v5a2 2 0 1 0 4 0v-5a2 2 0 0 0-2-2zM4 7.5a.5.5 0 0 0-1 0 5 5 0 0 0 4.5 4.975V14H6a.5.5 0 0 0 0 1h4a.5.5 0 0 0 0-1H8.5v-1.525A5 5 0 0 0 13 7.5a.5.5 0 0 0-1 0 4 4 0 1 1-8 0z"/>
        </svg>
      </div>
      <div class="icon-btn send" title="send">
        <svg viewBox="0 0 16 16" fill="currentColor">
          <path d="M1.5 1.5l13 6.5-13 6.5 2.5-6.5-2.5-6.5z"/>
        </svg>
      </div>
    </div>
  </div>
</body></html>"""


class Api:
    def close(self) -> None:
        webview.windows[0].destroy()


def main() -> int:
    win = webview.create_window(
        title="Pax AI",
        html=HTML,
        width=360,
        height=500,
        frameless=True,
        on_top=True,
        easy_drag=True,
        resizable=True,
        background_color="#07090d",
        js_api=Api(),
    )
    sys.stderr.write(f"[spike] window created: {win}\n")
    webview.start(debug=False, gui="edgechromium")
    sys.stderr.write("[spike] window closed cleanly\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
