"""Static-HTML visualization for a compiled lore and its sessions.

Reached by explicit import (``import lore.viz``) to keep ``import lore``
minimal — nothing here is needed for validation. Zero runtime dependencies
beyond the core (stdlib ``json`` + ``importlib.resources``).

- :func:`spec_json` renders a :class:`~lore.compile.Guard` as the World-tab
  JSON payload.
- :class:`TraceRecorder` implements the ``recorder=`` seam of
  ``guard.session()`` and accumulates the Timeline-tab event payload.
- :func:`to_html` writes one self-contained HTML file (World always; Timeline
  from a trace; State from a trace or a bare ``session.snapshot()`` blob).
"""

from .html import to_html
from .record import TraceRecorder
from .spec import spec_json

SPEC_FORMAT = 1
TRACE_FORMAT = 1

__all__ = ["SPEC_FORMAT", "TRACE_FORMAT", "TraceRecorder", "spec_json", "to_html"]
