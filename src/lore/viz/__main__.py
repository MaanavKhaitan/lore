"""``python -m lore.viz`` — export the viewer HTML from the command line.

Examples::

    python -m lore.viz examples/legal/world.py -o legal.html
    python -m lore.viz examples/legal/world.py --trace run.json -o run.html
    python -m lore.viz myapp.world:guard --snapshot blob.json -o state.html

The ``world`` argument names a compiled guard: either ``pkg.module:attr`` or
``path/to/world.py:attr`` (``attr`` defaults to ``guard``). File paths are
loaded with the file's directory on ``sys.path``, matching how the examples
import their own ``world`` module.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path

from ..compile import Guard
from ..schema import LoreError
from .html import to_html


def _load_guard(spec: str) -> Guard:
    target, _, attr = spec.partition(":")
    attr = attr or "guard"
    if target.endswith(".py") or "/" in target or "\\" in target:
        path = Path(target).resolve()
        if not path.exists():
            raise SystemExit(f"error: no such file: {target}")
        sys.path.insert(0, str(path.parent))
        module_spec = importlib.util.spec_from_file_location(path.stem, path)
        assert module_spec is not None and module_spec.loader is not None
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[path.stem] = module
        module_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    obj = getattr(module, attr, None)
    if obj is None:
        raise SystemExit(f"error: {target} has no attribute {attr!r}")
    if isinstance(obj, Guard):
        return obj
    compiled = getattr(obj, "compile", None)  # a Lore compiles to a Guard
    if callable(compiled):
        return compiled()
    raise SystemExit(f"error: {spec} is neither a compiled Guard nor a Lore")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m lore.viz",
        description="Export a self-contained HTML viewer for a lore world.",
    )
    parser.add_argument(
        "world",
        help='compiled guard: "pkg.module:attr" or "path/to/world.py:attr" '
        '(attr defaults to "guard"; a Lore is compiled automatically)',
    )
    parser.add_argument("-o", "--out", required=True, help="output HTML path")
    parser.add_argument("--trace", help="trace JSON written by TraceRecorder.dumps()")
    parser.add_argument("--snapshot", help="blob from session.snapshot()")
    parser.add_argument("--title", help="page title")
    args = parser.parse_args(argv)

    guard = _load_guard(args.world)
    trace = Path(args.trace).read_text(encoding="utf-8") if args.trace else None
    snapshot = Path(args.snapshot).read_text(encoding="utf-8") if args.snapshot else None
    try:
        out = to_html(guard, trace=trace, snapshot=snapshot, out=args.out, title=args.title)
    except LoreError as exc:
        raise SystemExit(f"error: {exc}") from exc
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
