"""Typed lookups safely follow relations in potentially invalid proposals."""

from lore import Entity, Lore, Relation

lore = Lore("typed-graph")


@lore.entity
class Target(Entity):
    name: str


@lore.entity
class SpecialTarget(Target):
    rank: int


@lore.entity
class Other(Entity):
    pass


@lore.entity
class Link(Entity):
    target: Relation[Target]


@lore.rule(message="Target name must not be empty.")
def target_named(link: Link, graph):
    target = graph.get(link.target, Target)
    return target is None or bool(target.name)


guard = lore.compile()


def test_typed_get_accepts_subclasses_and_preserves_untyped_get():
    expected = SpecialTarget(id="target", name="name", rank=1)
    session = guard.session(seed=[expected, Other(id="other")])
    assert session.graph.get("target") == expected
    assert session.graph.get("target", Target) == expected
    assert session.graph.get("target", SpecialTarget) == expected
    assert session.graph.get("other", Target) is None
    assert session.graph.get("missing", Target) is None


def test_typed_get_reads_staged_facts():
    session = guard.session()
    assert session.propose(Target(id="target", name="name"), Link(id="link", target="target")).ok
    assert session.graph.get("target", Target).name == "name"
    session.rollback()
    assert session.graph.get("target", Target) is None


def test_wrong_type_in_same_batch_produces_range_verdict():
    session = guard.session()
    verdict = session.try_commit(Other(id="other"), Link(id="link", target="other"))
    assert not verdict.ok
    assert {v.check for v in verdict.rejects} == {"range"}
    assert session.facts == ()
