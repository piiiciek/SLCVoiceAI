"""A small always-on-top panel for running and tuning the bridge.

Built on tkinter so it adds no dependencies, and kept narrow enough to sit
beside a full-screen simulator.

The point of this window is to answer, without reading a log file:

* is the bridge actually listening, and on which device
* what did it hear, what did it match, and did it press anything
* why did it refuse - the runner-up score usually explains it
* what is SLC offering right now

It also takes typed input, so the whole matching chain can be exercised
without a microphone and without being in a flight.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk

from .config import Config
from .slc_ui import SlcUI, UIAUnavailable

log = logging.getLogger(__name__)

BG = "#11161c"
FG = "#d6dde5"
MUTED = "#7c8899"
ACCENT = "#4fa3ff"
OK = "#4ec46e"
WARN = "#e0a63c"
BAD = "#e0604c"
FONT = ("Consolas", 9)
FONT_UI = ("Segoe UI", 9)


class QueueHandler(logging.Handler):
    """Funnel log records to the GUI thread."""

    def __init__(self, sink: queue.Queue):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        # Never let a UI problem take the bridge down.
        try:
            self.sink.put_nowait(record)
        except Exception:
            pass


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ui = SlcUI(cfg.slc.process_name)
        self.bridge = None
        self.ptt = None
        self.records: queue.Queue = queue.Queue()
        self._actions = []

        self.root = tk.Tk()
        self.root.title("SLCVoiceAI")
        self.root.configure(bg=BG)
        self.root.geometry("560x680")
        self.root.attributes("-topmost", True)
        self._build()
        self._attach_logging()
        self.root.after(120, self._drain)
        self.root.after(200, self._refresh_actions)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=FONT_UI)
        style.configure("TButton", font=FONT_UI)
        style.configure("TCheckbutton", background=BG, foreground=FG, font=FONT_UI)
        style.configure("Horizontal.TScale", background=BG)

        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")

        self.status = tk.Label(top, text="●  stopped", bg=BG, fg=MUTED,
                               font=("Segoe UI", 10, "bold"))
        self.status.pack(side="left")

        self.btn = ttk.Button(top, text="Start listening", command=self._toggle)
        self.btn.pack(side="right")

        self.subtitle = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=FONT,
                                 anchor="w", padx=10)
        self.subtitle.pack(fill="x")

        # -- controls
        ctrl = ttk.Frame(self.root, padding=(10, 6))
        ctrl.pack(fill="x")

        self.dry = tk.BooleanVar(value=self.cfg.behaviour.dry_run)
        ttk.Checkbutton(ctrl, text="Dry run (decide, never press)",
                        variable=self.dry, command=self._sync_dry).pack(side="left")

        tk.Label(ctrl, text="min confidence", bg=BG, fg=MUTED,
                 font=FONT).pack(side="left", padx=(16, 4))
        self.thresh = tk.DoubleVar(value=self.cfg.behaviour.min_confidence)
        scale = ttk.Scale(ctrl, from_=0.3, to=0.95, variable=self.thresh,
                          command=self._sync_threshold, length=110)
        scale.pack(side="left")
        self.thresh_lbl = tk.Label(ctrl, text="{:.2f}".format(self.thresh.get()),
                                   bg=BG, fg=ACCENT, font=FONT, width=5)
        self.thresh_lbl.pack(side="left")

        # -- typed test
        test = ttk.Frame(self.root, padding=(10, 4))
        test.pack(fill="x")
        tk.Label(test, text="Try a phrase without speaking:", bg=BG, fg=MUTED,
                 font=FONT).pack(anchor="w")
        row = ttk.Frame(test)
        row.pack(fill="x", pady=(3, 0))
        self.entry = tk.Entry(row, bg="#1b222b", fg=FG, insertbackground=FG,
                              relief="flat", font=FONT)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda _e: self._try_phrase())
        ttk.Button(row, text="Match", command=self._try_phrase).pack(side="left", padx=(6, 0))

        # -- panes
        panes = ttk.Frame(self.root, padding=(10, 8))
        panes.pack(fill="both", expand=True)

        tk.Label(panes, text="ACTIVITY", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.feed = tk.Text(panes, height=16, bg="#161c24", fg=FG, relief="flat",
                            font=FONT, wrap="word", padx=8, pady=6)
        self.feed.pack(fill="both", expand=True)
        self.feed.tag_config("muted", foreground=MUTED)
        self.feed.tag_config("ok", foreground=OK)
        self.feed.tag_config("warn", foreground=WARN)
        self.feed.tag_config("bad", foreground=BAD)
        self.feed.tag_config("accent", foreground=ACCENT)
        self.feed.configure(state="disabled")

        tk.Label(panes, text="SLC IS OFFERING", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(8, 0))
        self.actions_box = tk.Listbox(panes, height=8, bg="#161c24", fg=FG,
                                      relief="flat", font=FONT,
                                      selectbackground=ACCENT, activestyle="none")
        self.actions_box.pack(fill="both", expand=True)

    # -- logging bridge ----------------------------------------------------
    def _attach_logging(self) -> None:
        handler = QueueHandler(self.records)
        handler.setLevel(logging.INFO)
        root = logging.getLogger("slcvoiceai")
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    def _write(self, text: str, tag: str = "") -> None:
        self.feed.configure(state="normal")
        self.feed.insert("end", text + "\n", tag)
        # Keep the buffer from growing without bound over a long flight.
        if int(self.feed.index("end-1c").split(".")[0]) > 400:
            self.feed.delete("1.0", "150.0")
        self.feed.see("end")
        self.feed.configure(state="disabled")

    def _drain(self) -> None:
        while True:
            try:
                record = self.records.get_nowait()
            except queue.Empty:
                break
            self._render(record)
        self.root.after(120, self._drain)

    def _render(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        stamp = "{:%H:%M:%S}".format(
            __import__("datetime").datetime.fromtimestamp(record.created))

        if record.levelno >= logging.ERROR:
            self._write("{s}  {m}".format(s=stamp, m=msg), "bad")
        elif record.levelno >= logging.WARNING:
            self._write("{s}  {m}".format(s=stamp, m=msg), "warn")
        elif "Pressed" in msg:
            self._write("{s}  {m}".format(s=stamp, m=msg), "ok")
        elif "DRY RUN" in msg:
            self._write("{s}  {m}".format(s=stamp, m=msg), "accent")
        elif "Transcribed" in msg or "Captured" in msg:
            self._write("{s}  {m}".format(s=stamp, m=msg))
        elif "Below confidence" in msg or "Declined" in msg:
            self._write("{s}  {m}".format(s=stamp, m=msg), "warn")
        elif "Ready" in msg:
            self._write("{s}  {m}".format(s=stamp, m=msg), "ok")
        else:
            self._write("{s}  {m}".format(s=stamp, m=msg), "muted")

    # -- actions pane ------------------------------------------------------
    def _refresh_actions(self) -> None:
        try:
            if self.ui.is_running():
                self._actions = self.ui.list_actions()
                names = [a.name for a in self._actions]
            else:
                self._actions = []
                names = ["(SLC is not running)"]
        except UIAUnavailable:
            # Transient; keep showing the last good list rather than blanking
            # the panel every time a COM call hiccups.
            names = list(self.actions_box.get(0, "end")) or ["(UI scan failed, retrying)"]
        except Exception as exc:
            self._actions = []
            names = ["(scan failed: {e})".format(e=exc)]

        current = list(self.actions_box.get(0, "end"))
        if current != names:
            self.actions_box.delete(0, "end")
            for n in names:
                self.actions_box.insert("end", n)
        # 2.5s: a scan costs ~1.7s, so anything tighter just burns CPU.
        self.root.after(2500, self._refresh_actions)

    # -- controls ----------------------------------------------------------
    def _sync_dry(self) -> None:
        self.cfg.behaviour.dry_run = bool(self.dry.get())
        if self.bridge is not None:
            self.bridge.cfg.behaviour.dry_run = self.cfg.behaviour.dry_run
        self._write("-- dry run {}".format(
            "on: nothing will be pressed" if self.cfg.behaviour.dry_run
            else "off: matches will be pressed for real"), "accent")

    def _sync_threshold(self, _value=None) -> None:
        value = round(float(self.thresh.get()), 2)
        self.thresh_lbl.configure(text="{:.2f}".format(value))
        self.cfg.behaviour.min_confidence = value
        if self.bridge is not None:
            self.bridge.cfg.behaviour.min_confidence = value
            router = getattr(self.bridge, "router", None)
            if hasattr(router, "min_confidence"):
                router.min_confidence = value

    def _try_phrase(self) -> None:
        said = self.entry.get().strip()
        if not said:
            return
        if not self._actions:
            self._write("-- no actions to match against (is SLC running?)", "warn")
            return

        router = getattr(self.bridge, "router", None)
        if router is None:
            from .intent import build_router
            router = build_router(self.cfg)

        self._write('-- typed: "{s}"'.format(s=said), "accent")
        if hasattr(router, "rank"):
            for score, _i, action in router.rank(said, self._actions)[:4]:
                passes = score >= self.cfg.behaviour.min_confidence
                self._write("     {:.2f}  {:<34} {}".format(
                    score, action.name, "PASS" if passes else "below floor"),
                    "ok" if passes else "muted")
        else:
            decision = router.decide(said, self._actions)
            if decision.action_index is None:
                self._write("     declined: {r}".format(r=decision.reasoning), "warn")
            else:
                self._write("     {:.2f}  {}".format(
                    decision.confidence,
                    self._actions[decision.action_index].name), "ok")

    # -- bridge lifecycle --------------------------------------------------
    def _toggle(self) -> None:
        if self.bridge is None:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        self.btn.configure(state="disabled", text="Starting...")
        self.status.configure(text="●  loading Whisper", fg=WARN)
        self._write("-- loading the speech model, this takes a few seconds", "muted")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self) -> None:
        try:
            from .app import Bridge
            from .audio import PushToTalk

            bridge = Bridge(self.cfg)
            ptt = PushToTalk(self.cfg.audio, on_talk_start=bridge.prescan)
            ptt.start()
            self.bridge, self.ptt = bridge, ptt
            self.root.after(0, self._started)
            for captured_at, clip in ptt.clips():
                try:
                    bridge.handle(clip, captured_at)
                except Exception:
                    log.exception("Failed to handle an utterance; continuing.")
        except Exception as exc:
            log.error("Could not start: %s", exc)
            self.root.after(0, self._start_failed)

    def _started(self) -> None:
        device = getattr(getattr(self.bridge, "stt", None), "_device", "?")
        self.status.configure(text="●  listening", fg=OK)
        self.subtitle.configure(
            text="hold {key} and speak   ·   whisper {model} on {dev}   ·   {backend}".format(
                key=self.cfg.audio.ptt_key, model=self.cfg.stt.model,
                dev=device, backend=self.cfg.intent.backend))
        self.btn.configure(state="normal", text="Stop")

    def _start_failed(self) -> None:
        self.status.configure(text="●  failed to start", fg=BAD)
        self.btn.configure(state="normal", text="Start listening")
        self.bridge = None

    def _stop(self) -> None:
        if self.ptt is not None:
            self.ptt.stop()
        self.bridge, self.ptt = None, None
        self.status.configure(text="●  stopped", fg=MUTED)
        self.subtitle.configure(text="")
        self.btn.configure(text="Start listening")
        self._write("-- stopped listening", "muted")

    def _on_close(self) -> None:
        if self.ptt is not None:
            self.ptt.stop()
        self.root.destroy()

    def run(self) -> int:
        self._write("SLCVoiceAI ready. Start SLC, get into a flight, then press "
                    "Start listening.", "muted")
        self._write("You can also type a phrase above to test matching without "
                    "a microphone.", "muted")
        self.root.mainloop()
        return 0


def run(cfg: Config) -> int:
    return App(cfg).run()
