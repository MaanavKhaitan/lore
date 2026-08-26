"""TraceRecorder — the ``recorder=`` seam, accumulated as the Timeline payload.

Facts are encoded byte-identically to the snapshot codec
(:func:`lore.session.encode_fact`), so the viewer's single fact decoder serves
trace events and raw ``snapshot()`` blobs alike. A recorder must never break
the session it observes: an attribute value the field's annotation cannot
serialize is degraded to ``{"value": repr(...), "repr": true}`` instead of
raising.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from ..store import AttrFact, Fact
from ..verdict import Verdict

if TYPE_CHECKING:
    from ..compile import Guard
    from ..session import Session


class TraceRecorder:
    """Records one session's lifecycle as JSON-safe events.

    Pass to ``guard.session(recorder=...)`` (or ``guard.restore``), then embed
    with :func:`lore.viz.to_html` or persist ``dumps()`` for a later export::

        recorder = TraceRecorder()
        session = guard.session(seed=[...], recorder=recorder)
        ...
        to_html(guard, trace=recorder, out="run.html")

    One recorder records one session; attach a fresh instance per session.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.lore: str | None = None
        self.fingerprint: str | None = None
        self._adapters: dict[str, TypeAdapter[Any]] = {}

    # --- the SessionRecorder protocol ----------------------------------------

    def on_event(self, kind: str, session: "Session", **data: Any) -> None:
        guard = session._guard
        self.lore = guard.name
        self.fingerprint = guard.fingerprint
        event: dict[str, Any] = {"type": kind}
        if "facts" in data:
            event["facts"] = [self._encode_fact(guard, f) for f in data["facts"]]
        if "step" in data:
            event["step"] = data["step"]
        if "verdict" in data:
            event["verdict"] = self._encode_verdict(guard, data["verdict"])
        self.events.append(event)

    # --- payload --------------------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        """The trace as one JSON-safe dict (``format: 1``)."""
        return {
            "format": 1,
            "kind": "lore-trace",
            "lore": self.lore,
            "fingerprint": self.fingerprint,
            "events": list(self.events),
        }

    def dumps(self) -> str:
        """The trace as a compact JSON string (for a file or a blob store)."""
        return json.dumps(self.to_payload(), separators=(",", ":"))

    # --- encoding ---------------------------------------------------------------

    def _encode_fact(self, guard: "Guard", fact: Fact) -> dict[str, Any]:
        from ..session import encode_fact

        try:
            return encode_fact(guard, fact, self._adapters)
        except Exception:
            if not isinstance(fact, AttrFact):
                raise
            return {
                "kind": "attr",
                "subject": fact.subject_id,
                "attr": fact.attr,
                "value": repr(fact.value),
                "repr": True,
                "source": fact.source,
                "step": fact.step,
            }

    def _encode_verdict(self, guard: "Guard", verdict: Verdict) -> dict[str, Any]:
        return {
            "ok": verdict.ok,
            "repairPrompt": verdict.repair_prompt(),
            "violations": [
                {
                    "check": v.check,
                    "severity": v.severity,
                    "message": v.message,
                    "subjects": list(v.subjects),
                    "provenance": [self._encode_fact(guard, f) for f in v.provenance],
                }
                for v in verdict.violations
            ],
        }
