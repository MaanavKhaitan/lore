"""to_html: marker injection, script-block escaping, fingerprint refusal, the
checked-in assets contract, and the CLI."""

import json
import re

import pytest

from lore import Entity, Lore, LoreError
from lore.viz import SPEC_FORMAT, TraceRecorder, to_html

lore = Lore("viz-html-test")


@lore.entity
class Note(Entity):
    text: str


guard = lore.compile()


def _block(page: str, block_id: str) -> object:
    match = re.search(
        rf'<script type="application/json" id="{block_id}">(.*?)</script>', page, re.DOTALL
    )
    assert match, f"missing JSON block {block_id}"
    return json.loads(match.group(1))


def _render(tmp_path, **kwargs):
    out = to_html(guard, out=tmp_path / "out.html", **kwargs)
    return out.read_text(encoding="utf-8")


MARKERS = (
    "__LORE_TITLE__",
    "__LORE_CSS__",
    "__LORE_SPEC__",
    "__LORE_TRACE__",
    "__LORE_SNAPSHOT__",
    "__LORE_JS__",
)


def test_spec_only_export(tmp_path):
    page = _render(tmp_path)
    for marker in MARKERS:  # every marker replaced (the JS bundle may use the
        assert marker not in page  # bare "__LORE_" prefix for dev-mode detection)
    assert _block(page, "lore-spec")["lore"] == "viz-html-test"
    assert _block(page, "lore-trace") is None
    assert _block(page, "lore-snapshot") is None
    assert "<title>lore — viz-html-test</title>" in page


def test_trace_export_accepts_recorder_dict_and_string(tmp_path):
    recorder = TraceRecorder()
    session = guard.session(recorder=recorder)
    session.try_commit(Note(id="n1", text="hi"))
    for trace in (recorder, recorder.to_payload(), recorder.dumps()):
        page = _render(tmp_path, trace=trace)
        payload = _block(page, "lore-trace")
        assert [e["type"] for e in payload["events"]] == ["session_start", "propose", "commit"]


def test_snapshot_only_export(tmp_path):
    session = guard.session()
    session.try_commit(Note(id="n1", text="hi"))
    page = _render(tmp_path, snapshot=session.snapshot(), title="Production run 42")
    assert _block(page, "lore-trace") is None
    facts = _block(page, "lore-snapshot")["facts"]
    assert {"kind": "type", "node": "n1", "class": "Note", "source": "asserted", "step": 1} in facts
    assert "<title>Production run 42</title>" in page


def test_script_terminator_is_escaped(tmp_path):
    session = guard.session()
    session.try_commit(Note(id="n1", text="</script><script>alert(1)</script>"))
    page = _render(tmp_path, snapshot=session.snapshot())
    assert "</script><script>alert(1)" not in page  # raw terminator never appears
    assert "<\\/script>" in page  # escaped JSON form does
    assert _block(page, "lore-snapshot")  # and the block still parses


def test_fingerprint_mismatch_raises(tmp_path):
    other = Lore("viz-html-other")

    @other.entity
    class Thing(Entity):
        name: str

    other_guard = other.compile()
    session = other_guard.session()
    session.try_commit(Thing(id="t1", name="x"))
    with pytest.raises(LoreError, match="fingerprint"):
        to_html(guard, snapshot=session.snapshot(), out=tmp_path / "out.html")
    recorder = TraceRecorder()
    other_guard.session(recorder=recorder)
    with pytest.raises(LoreError, match="fingerprint"):
        to_html(guard, trace=recorder, out=tmp_path / "out.html")


def test_checked_in_assets_exist_with_markers_and_format_sentinel():
    from importlib.resources import files

    assets = files("lore.viz").joinpath("assets")
    template = assets.joinpath("template.html").read_text(encoding="utf-8")
    for marker in (
        "__LORE_TITLE__",
        "/*__LORE_CSS__*/",
        "__LORE_SPEC__",
        "__LORE_TRACE__",
        "__LORE_SNAPSHOT__",
        "/*__LORE_JS__*/",
    ):
        assert marker in template, f"template.html lost the {marker} marker"
    bundle = assets.joinpath("viz.js").read_text(encoding="utf-8")
    # The bundle states which payload format it reads; a format bump in Python
    # without `npm run build` in ui/ fails here instead of at view time.
    assert f"lore-spec-format:{SPEC_FORMAT}" in bundle


def test_cli_exports_from_a_world_file(tmp_path, capsys):
    world = tmp_path / "world.py"
    world.write_text(
        "from lore import Entity, Lore\n"
        "lore = Lore('cli-test')\n"
        "@lore.entity\n"
        "class Thing(Entity):\n"
        "    name: str\n"
        "guard = lore.compile()\n"
    )
    out = tmp_path / "out.html"
    from lore.viz.__main__ import main

    main([str(world), "-o", str(out)])
    page = out.read_text(encoding="utf-8")
    assert _block(page, "lore-spec")["lore"] == "cli-test"
    assert str(out) in capsys.readouterr().out
