"""A small always-on-top panel for running and tuning the bridge.

Built on tkinter so it adds no dependencies, and kept narrow enough to sit
beside a full-screen simulator.

The point of this window is to answer, without reading a log file:

* is the bridge actually listening, and on which device
* what did it hear, what did it match, and did it press anything
* why did it refuse - the runner-up score usually explains it

It also takes typed input, so the whole matching chain can be exercised
without a microphone and without being in a flight. That reads SLC when
you ask it to and not before: a live list of SLC's buttons, refreshed on a
timer, meant the panel scanned SLC continuously for as long as it was open
and competed with the scans that carry a command.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import i18n
from .config import Config
from .i18n import t
from .slc_ui import SlcUI

log = logging.getLogger(__name__)


def _running_version() -> str:
    from . import __version__
    return __version__

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
        i18n.set_language(cfg.ui.language)
        #: Which status phrase is showing, so a language change can redraw it.
        self._status_key = "status.stopped"
        self._status_colour = MUTED
        self._update_version = None

        self.root = tk.Tk()
        self.root.title("SLCVoiceAI")
        self.root.configure(bg=BG)
        self.root.geometry("560x680")
        self.root.attributes("-topmost", True)
        self._build()
        self._attach_logging()
        self.root.after(120, self._drain)
        self.root.after(400, self._check_for_updates)
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

        self.status = tk.Label(top, text=t("status.stopped"), bg=BG, fg=MUTED,
                               font=("Segoe UI", 10, "bold"))
        self.status.pack(side="left")

        self.btn = ttk.Button(top, text=t("button.start"), command=self._toggle)
        self.btn.pack(side="right")

        self.subtitle = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=FONT,
                                 anchor="w", padx=10)
        self.subtitle.pack(fill="x")

        # Stays out of the layout entirely until there is something to say.
        self.update_banner = tk.Label(
            self.root, text="", bg="#1d2a1f", fg=OK, font=FONT_UI,
            anchor="w", padx=10, pady=5, cursor="hand2")
        self.update_banner.bind(
            "<Button-1>", lambda _e: self._open_repository())

        # -- controls
        ctrl = ttk.Frame(self.root, padding=(10, 6))
        ctrl.pack(fill="x")

        self.dry = tk.BooleanVar(value=self.cfg.behaviour.dry_run)
        self.dry_check = ttk.Checkbutton(
            ctrl, text=t("control.dry_run"), variable=self.dry,
            command=self._sync_dry)
        self.dry_check.pack(side="left")

        self.conf_label = tk.Label(ctrl, text=t("control.confidence"), bg=BG,
                                   fg=MUTED, font=FONT)
        self.conf_label.pack(side="left", padx=(16, 4))
        self.thresh = tk.DoubleVar(value=self.cfg.behaviour.min_confidence)
        scale = ttk.Scale(ctrl, from_=0.3, to=0.95, variable=self.thresh,
                          command=self._sync_threshold, length=110)
        scale.pack(side="left")
        self.thresh_lbl = tk.Label(ctrl, text="{:.2f}".format(self.thresh.get()),
                                   bg=BG, fg=ACCENT, font=FONT, width=5)
        self.thresh_lbl.pack(side="left")

        self.lang_label = tk.Label(ctrl, text=t("control.language"), bg=BG,
                                   fg=MUTED, font=FONT)
        self.lang_label.pack(side="left", padx=(16, 4))
        names = i18n.available()
        self.lang = tk.StringVar(value=names.get(i18n.current(), "English"))
        picker = ttk.OptionMenu(ctrl, self.lang, self.lang.get(),
                                *names.values(), command=self._sync_language)
        picker.configure(width=8)
        picker.pack(side="left")

        # -- typed test
        test = ttk.Frame(self.root, padding=(10, 4))
        test.pack(fill="x")
        self.test_label = tk.Label(test, text=t("test.prompt"), bg=BG, fg=MUTED,
                                   font=FONT)
        self.test_label.pack(anchor="w")
        row = ttk.Frame(test)
        row.pack(fill="x", pady=(3, 0))
        self.entry = tk.Entry(row, bg="#1b222b", fg=FG, insertbackground=FG,
                              relief="flat", font=FONT)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda _e: self._try_phrase())
        self.match_btn = ttk.Button(row, text=t("test.match"),
                                    command=self._try_phrase)
        self.match_btn.pack(side="left", padx=(6, 0))

        # -- panes
        panes = ttk.Frame(self.root, padding=(10, 8))
        panes.pack(fill="both", expand=True)

        self.activity_label = tk.Label(panes, text=t("pane.activity"), bg=BG,
                                       fg=MUTED, font=("Segoe UI", 8, "bold"))
        self.activity_label.pack(anchor="w")
        self.feed = tk.Text(panes, height=16, bg="#161c24", fg=FG, relief="flat",
                            font=FONT, wrap="word", padx=8, pady=6)
        self.feed.pack(fill="both", expand=True)
        self.feed.tag_config("muted", foreground=MUTED)
        self.feed.tag_config("ok", foreground=OK)
        self.feed.tag_config("warn", foreground=WARN)
        self.feed.tag_config("bad", foreground=BAD)
        self.feed.tag_config("accent", foreground=ACCENT)
        self.feed.configure(state="disabled")

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

    # -- update notice ------------------------------------------------------
    def _check_for_updates(self) -> None:
        """Ask GitHub, off the UI thread, and only speak up if behind."""
        if not self.cfg.behaviour.check_for_updates:
            return

        def work():
            from . import update

            newer = update.check()
            if newer:
                self.root.after(0, lambda: self._show_update(newer))

        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, version: str) -> None:
        from . import update

        self._update_version = version
        self.update_banner.configure(
            text=t("update.banner", new=version, old=_running_version()))
        # Under the status line, above everything else, so it is the first
        # thing read - and it was never packed before now, so a current copy
        # never gives up a pixel to it.
        self.update_banner.pack(fill="x", after=self.subtitle)
        self._write(t("update.feed", new=version, old=_running_version(),
                      url=update.RELEASES_URL), "ok")

    def _open_repository(self) -> None:
        import webbrowser

        from . import update
        try:
            webbrowser.open(update.RELEASES_URL)
        except Exception as exc:  # pragma: no cover - browser is the OS's
            log.warning("Could not open %s: %s", update.RELEASES_URL, exc)

    # -- reading SLC on demand ----------------------------------------------
    def _scan_in_background(self, then) -> None:
        """Read SLC once, off the UI thread, and hand the result to `then`.

        The panel used to keep a live list of SLC's buttons, refreshed every
        2.5 seconds for as long as it was open. That was affordable when a
        scan cost 1.7s at the launcher; in a flight a scan costs 3-8s, so the
        panel was reading SLC essentially without pause and competing with
        the scans that actually carry a command. Now nothing scans unless
        someone asks it to.
        """
        def work():
            from .app import _Scan

            scan = _Scan(self.ui)
            scan.start()
            try:
                actions = scan.result()
                error = None
            except Exception as exc:
                actions, error = [], exc
            self.root.after(0, lambda: then(actions, error))

        threading.Thread(target=work, daemon=True).start()

    # -- language ----------------------------------------------------------
    def _set_status(self, key: str, colour: str) -> None:
        """Remember which phrase is showing, so it can be redrawn."""
        self._status_key, self._status_colour = key, colour
        self.status.configure(text=t(key), fg=colour)

    def _sync_language(self, chosen: str) -> None:
        for code, name in i18n.available().items():
            if name == chosen:
                i18n.set_language(code)
                self.cfg.ui.language = code
                break
        self._retranslate()
        self._write(t("feed.language_changed", name=chosen), "accent")

    def _retranslate(self) -> None:
        """Re-label everything in place.

        The activity feed is left as it stands: those lines are a record of
        what happened, and rewriting history in a new language would be a
        strange thing for a log to do. New entries arrive translated.
        """
        self.status.configure(text=t(self._status_key), fg=self._status_colour)
        self.btn.configure(text=t("button.stop") if self.bridge is not None
                           else t("button.start"))
        self.dry_check.configure(text=t("control.dry_run"))
        self.conf_label.configure(text=t("control.confidence"))
        self.lang_label.configure(text=t("control.language"))
        self.test_label.configure(text=t("test.prompt"))
        self.match_btn.configure(text=t("test.match"))
        self.activity_label.configure(text=t("pane.activity"))
        if self.bridge is not None:
            self._describe_bridge()
        if self._update_version:
            self.update_banner.configure(text=t(
                "update.banner", new=self._update_version,
                old=_running_version()))

    # -- controls ----------------------------------------------------------
    def _sync_dry(self) -> None:
        self.cfg.behaviour.dry_run = bool(self.dry.get())
        if self.bridge is not None:
            self.bridge.cfg.behaviour.dry_run = self.cfg.behaviour.dry_run
        self._write(t("feed.dry_run", state=t("feed.dry_on")
                      if self.cfg.behaviour.dry_run else t("feed.dry_off")),
                    "accent")

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
        self._write(t("feed.typed", said=said), "accent")
        self._scan_in_background(lambda actions, error:
                                 self._show_ranking(said, actions, error))

    def _show_ranking(self, said: str, actions: list, error) -> None:
        if error is not None:
            self._write(t("feed.read_failed", error=error), "warn")
            return
        if not actions:
            self._write(t("feed.no_actions"), "warn")
            return

        router = getattr(self.bridge, "router", None)
        if router is None:
            from .intent import build_router
            router = build_router(self.cfg)

        if hasattr(router, "rank"):
            for score, _i, action in router.rank(said, actions)[:4]:
                passes = score >= self.cfg.behaviour.min_confidence
                self._write("     {:.2f}  {:<34} {}".format(
                    score, action.name,
                    t("feed.pass") if passes else t("feed.below_floor")),
                    "ok" if passes else "muted")
        else:
            decision = router.decide(said, actions)
            if decision.action_index is None:
                self._write(t("feed.declined", reason=decision.reasoning), "warn")
            else:
                self._write("     {:.2f}  {}".format(
                    decision.confidence,
                    actions[decision.action_index].name), "ok")

    # -- bridge lifecycle --------------------------------------------------
    def _toggle(self) -> None:
        if self.bridge is None:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        self.btn.configure(state="disabled", text=t("button.starting"))
        self._set_status("status.loading", WARN)
        self._write(t("feed.loading_model"), "muted")
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
        self._set_status("status.listening", OK)
        self._describe_bridge()
        self.btn.configure(state="normal", text=t("button.stop"))

    def _describe_bridge(self) -> None:
        """The subtitle line, rebuilt - it is also what a language change
        has to redraw, so it lives on its own."""
        device = getattr(getattr(self.bridge, "stt", None), "_device", "?")
        self.subtitle.configure(text=t(
            "subtitle.ready", key=self.cfg.audio.ptt_key,
            model=self.cfg.stt.model, dev=device,
            backend=self.cfg.intent.backend))

    def _start_failed(self) -> None:
        self._set_status("status.failed", BAD)
        self.btn.configure(state="normal", text=t("button.start"))
        self.bridge = None

    def _stop(self) -> None:
        if self.ptt is not None:
            self.ptt.stop()
        self.bridge, self.ptt = None, None
        self._set_status("status.stopped", MUTED)
        self.subtitle.configure(text="")
        self.btn.configure(text=t("button.start"))
        self._write(t("feed.stopped"), "muted")

    def _on_close(self) -> None:
        if self.ptt is not None:
            self.ptt.stop()
        self.root.destroy()

    def run(self) -> int:
        self._write(t("feed.welcome", key=self.cfg.audio.ptt_key), "muted")
        self._write(t("feed.welcome_typed"), "muted")
        self.root.mainloop()
        return 0


def run(cfg: Config) -> int:
    return App(cfg).run()
