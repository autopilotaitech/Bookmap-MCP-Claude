"""pywebview floating-window shell for Pax AI.

Loads http://127.0.0.1:<port>/ inside a frameless, on_top, draggable
WebView2 window. Designed to be launched by the PaxAILauncher Bookmap
addon (or manually via `python -m pax_ai --shell`).

Closing the window via the in-page X exits the process. The PaxAILauncher
addon calls ProcessBuilder.destroy() on Bookmap-side detach, which kills
this window from outside.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time

from . import DEFAULT_PORT


# Win32 constants for the on_top reassert ticker
_HWND_TOPMOST = -1
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010


def _reassert_topmost_thread(get_hwnd, stop_evt: threading.Event,
                              interval_s: float = 5.0) -> None:
    """Background ticker: every interval_s, re-apply HWND_TOPMOST.

    Some Bookmap fullscreen modes can cover an on_top window. Reasserting
    topmost periodically restores the float without stealing focus.
    """
    try:
        user32 = ctypes.windll.user32
    except (OSError, AttributeError):
        return
    while not stop_evt.is_set():
        try:
            hwnd = get_hwnd()
            if hwnd:
                user32.SetWindowPos(
                    int(hwnd), _HWND_TOPMOST, 0, 0, 0, 0,
                    _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
        except Exception:
            pass
        stop_evt.wait(interval_s)


class PaxAIShell:
    """Thin wrapper around pywebview's create_window + start loop.

    pywebview's `webview.start()` is blocking and must run on the main
    thread. The HTTP server runs in its own daemon thread, started BEFORE
    we call start().
    """

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self._stop_topmost = threading.Event()
        self._topmost_thread: threading.Thread | None = None

    def run(self) -> int:
        try:
            import webview
        except ImportError:
            sys.stderr.write("[shell] pywebview not installed; "
                              "run: mcp-server\\.venv\\Scripts\\pip install pywebview\n")
            return 2

        url = f"http://127.0.0.1:{self.port}/"

        class Api:
            def close(_self) -> None:
                w = webview.windows[0] if webview.windows else None
                if w is not None:
                    w.destroy()

        window = webview.create_window(
            title="Pax AI",
            url=url,
            width=360,
            height=500,
            frameless=True,
            on_top=True,
            easy_drag=True,
            resizable=True,
            background_color="#07090d",
            js_api=Api(),
        )

        # Start the topmost-reassert ticker on the FIRST window creation event.
        def _on_loaded() -> None:
            try:
                hwnd = window.native_handle  # set by pywebview after window opens
            except Exception:
                hwnd = None
            if hwnd:
                self._topmost_thread = threading.Thread(
                    target=_reassert_topmost_thread,
                    args=(lambda: window.native_handle, self._stop_topmost),
                    name="pax-ai-topmost",
                    daemon=True,
                )
                self._topmost_thread.start()
        try:
            window.events.loaded += _on_loaded
        except Exception:
            pass

        sys.stderr.write(f"[shell] opening floating window on {url}\n")
        try:
            webview.start(debug=False, gui="edgechromium")
        finally:
            self._stop_topmost.set()
        sys.stderr.write("[shell] window closed cleanly\n")
        return 0


def run(port: int = DEFAULT_PORT) -> int:
    return PaxAIShell(port=port).run()
