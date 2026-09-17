"""Entry point:  python -m slcvoiceai"""

from __future__ import annotations

import argparse
import sys

from . import config as config_module
from .app import Bridge, setup_logging


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="slcvoiceai",
        description="Speak naturally, in any language, to Self-Loading Cargo.")
    ap.add_argument("-c", "--config", default="config.toml",
                    help="path to config.toml (default: ./config.toml)")
    ap.add_argument("--gui", action="store_true",
                    help="open the control panel instead of running headless")
    ap.add_argument("--dry-run", action="store_true",
                    help="decide but never actually press anything")
    ap.add_argument("--list-actions", action="store_true",
                    help="print what SLC is offering right now, then exit")
    ap.add_argument("--list-devices", action="store_true",
                    help="print available input devices, then exit")
    args = ap.parse_args(argv)

    if args.list_devices:
        import sounddevice as sd
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                print("[{i}] {name}".format(i=idx, name=dev["name"]))
        return 0

    try:
        cfg = config_module.load(args.config)
    except (ValueError, OSError) as exc:
        print("Config error: {exc}".format(exc=exc), file=sys.stderr)
        return 2

    if args.dry_run:
        cfg.behaviour.dry_run = True

    setup_logging(cfg.behaviour.log_file)

    if args.list_actions:
        from .slc_ui import SlcUI
        ui = SlcUI(cfg.slc.process_name)
        if not ui.is_running():
            print("SLC is not running.")
            return 1
        actions = ui.list_actions()
        if not actions:
            print("SLC is running but offering no invokable controls right now.")
            return 0
        for i, action in enumerate(actions):
            print("{i:>3}. {name}   [{ctype}]   window={win}".format(
                i=i, name=action.name, ctype=action.control_type, win=action.window))
        return 0

    if cfg.needs_api_key:
        try:
            cfg.api_key  # fail fast with a clear message rather than mid-flight
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if args.gui:
        from .gui import run as run_gui
        return run_gui(cfg)

    return Bridge(cfg).run()


if __name__ == "__main__":
    raise SystemExit(main())
