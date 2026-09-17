"""Optional flight context from SLC's stream-data export.

Enable it in SLC: Settings -> Export Stream Data, pick a folder, and tick the
fields you want. SLC writes one small text file per field; we read them all
and hand the model a compact snapshot so it can disambiguate intent by
flight phase.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

MAX_FIELD_CHARS = 120


def read_flight_context(export_dir: str) -> dict[str, str]:
    """Read SLC's exported stream files into a {field: value} dict."""
    if not export_dir:
        return {}
    folder = Path(export_dir)
    if not folder.is_dir():
        log.warning("Stream export folder does not exist: %s", folder)
        return {}

    context: dict[str, str] = {}
    for path in sorted(folder.glob("*.txt")):
        try:
            value = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            log.debug("Could not read %s: %s", path, exc)
            continue
        if value:
            context[path.stem] = value[:MAX_FIELD_CHARS]
    return context


def format_context(context: dict[str, str]) -> str:
    if not context:
        return "(no flight data exported)"
    return "\n".join("{k}: {v}".format(k=k, v=v) for k, v in context.items())
