"""Session lifecycle: propose/commit/rollback, retry pollution, sources."""

import pytest

from lore import Entity, Lore, LoreError, Relation
from lore.store import EdgeFact, TypeFact

lore = Lore("session-test")


@lore.entity
class Target(Entity):
    pass


@lore.entity
class Item(Entity):
    name: str
    linked: Relation[Target] | None = None


guard = lore.compile()


def test_propose_commit_lifecycle():
    session = guard.session(seed=[Target(id="t1")])
    verdict = session.propose(Item(id="i1", name="widget", linked="t1"))
    assert verdict.ok
    session.commit()
    assert TypeFact("i1", "Item", "asserted") in session.facts
    assert EdgeFact("i1", "Item.linked", "t1", "asserted") in session.facts
    assert session.staged_facts == ()


def test_seed_facts_carry_seed_source_asserted_carry_asserted():
    session = guard.session(seed=[Target(id="t1")])
    session.propose(Item(id="i1", name="widget"))
    session.commit()
    sources = {f.node_id: f.source for f in session.facts if isinstance(f, TypeFact)}
    assert sources == {"t1": "seed", "i1": "asserted"}


def test_retry_pollution_rollback_leaves_zero_trace():
    session = guard.session(seed=[Target(id="t1")])
    before = session.facts
    verdict = session.propose(Item(id="bad", name="broken", linked="ghost"))
    assert not verdict.ok
    session.rollback()
    assert session.facts == before
    assert session.staged_facts == ()


def test_retry_pollution_next_propose_discards_bad_attempt():
    session = guard.session(seed=[Target(id="t1")])
    bad = session.propose(Item(id="bad", name="broken", linked="ghost"))
    assert not bad.ok
    good = session.propose(Item(id="good", name="fixed", linked="t1"))
    assert good.ok and good.violations == []  # the bad attempt must not re-fire
    session.commit()
    assert not any("bad" in getattr(f, "node_id", "") for f in session.facts)


def test_commit_on_failed_verdict_raises():
    session = guard.session(seed=[Target(id="t1")])
    session.propose(Item(id="bad", name="broken", linked="ghost"))
    with pytest.raises(LoreError, match="reject-severity violation"):
        session.commit()


def test_commit_with_nothing_staged_raises():
    session = guard.session()
    with pytest.raises(LoreError, match="nothing to commit"):
        session.commit()
    session.propose(Item(id="i1", name="widget"))
    session.commit()
    with pytest.raises(LoreError, match="nothing to commit"):
        session.commit()


def test_propose_replaces_previously_staged_facts():
    session = guard.session()
    session.propose(Item(id="first", name="a"))
    session.propose(Item(id="second", name="b"))
    session.commit()
    node_ids = {f.node_id for f in session.facts if isinstance(f, TypeFact)}
    assert node_ids == {"second"}


def test_optional_relation_left_none_produces_no_edge():
    session = guard.session()
    verdict = session.propose(Item(id="i1", name="widget"))
    assert verdict.ok
    assert not any(isinstance(f, EdgeFact) for f in session.staged_facts)


def test_reproposing_committed_output_is_ok_and_commit_is_noop():
    session = guard.session(seed=[Target(id="t1")])
    item = Item(id="i1", name="widget", linked="t1")
    session.propose(item)
    session.commit()
    before = session.facts
    verdict = session.propose(item)  # idempotent agent retry of the same output
    assert verdict.ok and session.staged_facts == ()
    session.commit()
    assert session.facts == before


def test_proposing_unregistered_entity_raises():
    class Rogue(Entity):
        pass

    session = guard.session()
    with pytest.raises(LoreError, match="not registered"):
        session.propose(Rogue(id="r1"))
