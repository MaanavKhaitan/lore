"""Assemble one self-contained HTML file from the checked-in viewer assets.

Python is the inliner: ``assets/template.html`` carries literal markers that
are replaced with the CSS/JS bundles and the JSON payloads — no server, no
node at install or run time. JSON is embedded in ``<script
type="application/json">`` blocks; the only sequence that could terminate such
a block is ``</``, which is escaped to the JSON-equivalent ``<\\/``.
"""

from __future__ import annotations

import html as _html
import json
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

from ..compile import Guard
from ..schema import LoreError
from .record import TraceRecorder
from .spec import spec_json

_ASSETS = "assets"


def _load_asset(name: str) -> str:
    return files("lore.viz").joinpath(f"{_ASSETS}/{name}").read_text(encoding="utf-8")


def _as_payload(value: Any, what: str) -> dict[str, Any]:
    if isinstance(value, TraceRecorder):
        return value.to_payload()
    if isinstance(value, (str, bytes)):
        try:
            data = json.loads(value)
        except (ValueError, UnicodeDecodeError) as exc:
            raise LoreError(f"not a lore {what} (invalid JSON): {exc}") from exc
    elif isinstance(value, Mapping):
        data = dict(value)
    else:
        raise LoreError(f"unsupported {what} input: {type(value).__name__}")
    if not isinstance(data, dict):
        raise LoreError(f"not a lore {what} (expected a JSON object)")
    return data


def _check_fingerprint(guard: Guard, payload: Mapping[str, Any], what: str) -> None:
    fingerprint = payload.get("fingerprint")
    if fingerprint != guard.fingerprint:
        raise LoreError(
            f"{what} was taken under lore {payload.get('lore')!r} with fingerprint "
            f"{fingerprint!r}, but rendering under {guard.name!r} with "
            f"{guard.fingerprint!r} — the lore has changed; export with the "
            "original definition"
        )


def _embed(payload: Mapping[str, Any] | None) -> str:
    if payload is None:
        return "null"
    return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")


def to_html(
    guard: Guard,
    *,
    trace: TraceRecorder | Mapping[str, Any] | str | bytes | None = None,
    snapshot: str | bytes | Mapping[str, Any] | None = None,
    out: str | Path,
    title: str | None = None,
) -> Path:
    """Write the viewer for ``guard`` to ``out`` and return its path.

    ``trace`` (a :class:`TraceRecorder`, its ``dumps()`` string, or its
    ``to_payload()`` dict) enables the Timeline and State tabs; ``snapshot``
    (a ``session.snapshot()`` blob) enables State alone — the production case
    where only the persisted blob survives. With neither, the file shows the
    World tab only. Raises :class:`~lore.schema.LoreError` when a trace or
    snapshot was produced under a different lore fingerprint.
    """
    trace_payload = None if trace is None else _as_payload(trace, "trace")
    if trace_payload is not None:
        _check_fingerprint(guard, trace_payload, "trace")
    snapshot_payload = None if snapshot is None else _as_payload(snapshot, "session snapshot")
    if snapshot_payload is not None:
        _check_fingerprint(guard, snapshot_payload, "session snapshot")

    page = (
        _load_asset("template.html")
        .replace("__LORE_TITLE__", _html.escape(title or f"lore — {guard.name}"))
        .replace("/*__LORE_CSS__*/", _load_asset("viz.css"))
        .replace("__LORE_SPEC__", _embed(spec_json(guard)))
        .replace("__LORE_TRACE__", _embed(trace_payload))
        .replace("__LORE_SNAPSHOT__", _embed(snapshot_payload))
        .replace("/*__LORE_JS__*/", _load_asset("viz.js"))
    )
    out_path = Path(out)
    out_path.write_text(page, encoding="utf-8")
    return out_path
