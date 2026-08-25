"""Session lifecycle: propose/commit/rollback, the ergonomic wrappers
(check/try_commit/guarded), retry pollution, sources, and inspection."""

import pytest

from lore import Entity, Lore, LoreError, LoreViolation, Relation, one_of, relation
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


def test_commits_stamp_facts_with_consecutive_step_numbers():
    session = guard.session(seed=[Target(id="t1")])
    session.try_commit(Item(id="i1", name="first", linked="t1"))
    session.try_commit(Item(id="i2", name="second", linked="t1"))
    # Idempotent re-commit of already-committed facts stages nothing and must
    # not consume a step number.
    session.try_commit(Item(id="i2", name="second", linked="t1"))
    session.try_commit(Item(id="i3", name="third", linked="t1"))
    steps = {f.node_id: f.step for f in session.facts if isinstance(f, TypeFact)}
    assert steps == {"t1": 0, "i1": 1, "i2": 2, "i3": 3}


def test_proposing_unregistered_entity_raises():
    class Rogue(Entity):
        pass

    session = guard.session()
    with pytest.raises(LoreError, match="not registered"):
        session.propose(Rogue(id="r1"))


# --- seed validation ----------------------------------------------------------


def test_invalid_seed_raises_listing_violations():
    with pytest.raises(LoreError, match="invalid seed") as excinfo:
        guard.session(seed=[Item(id="i1", name="broken", linked="ghost")])
    assert "i1 refers to ghost via Item.linked" in str(excinfo.value)


def test_validate_seed_false_opts_out():
    session = guard.session(
        seed=[Item(id="i1", name="broken", linked="ghost")], validate_seed=False
    )
    # The blunt closed-world rule still applies afterwards: the pre-existing
    # inconsistency doesn't re-fire on unrelated proposals.
    assert session.propose(Item(id="i2", name="fine")).ok


def test_seed_validation_runs_inference():
    lore_hr = Lore("seed-cycle-test")

    @lore_hr.entity
    class Emp(Entity):
        boss: Relation["Emp"] | None = relation(transitive=True, irreflexive=True, default=None)

    guard_hr = lore_hr.compile()
    guard_hr.session(seed=[Emp(id="a"), Emp(id="b", boss="a")])  # acyclic: fine
    with pytest.raises(LoreError, match="cycle"):
        guard_hr.session(seed=[Emp(id="a", boss="b"), Emp(id="b", boss="a")])


# --- check / try_commit / guarded --------------------------------------------


def test_check_is_a_preflight_with_zero_state_change():
    session = guard.session(seed=[Target(id="t1")])
    before = session.facts
    assert session.check(Item(id="i1", name="widget", linked="t1")).ok
    assert not session.check(Item(id="i2", name="broken", linked="ghost")).ok
    assert session.facts == before and session.staged_facts == ()


def test_check_preserves_a_pending_staged_proposal():
    session = guard.session(seed=[Target(id="t1")])
    pending = session.propose(Item(id="i1", name="widget", linked="t1"))
    staged_before = session.staged_facts
    assert session.check(Item(id="i2", name="other", linked="t1")).ok
    assert session.staged_facts == staged_before and session.last_verdict is pending
    session.commit()  # the pending proposal is still committable
    assert TypeFact("i1", "Item", "asserted") in session.facts


def test_check_is_allowed_inside_a_guarded_body():
    session = guard.session(seed=[Target(id="t1")])
    with session.guarded(Item(id="i1", name="widget", linked="t1")):
        assert not session.check(Item(id="i2", name="broken", linked="ghost")).ok
    assert TypeFact("i1", "Item", "asserted") in session.facts


def test_try_commit_commits_when_ok():
    session = guard.session(seed=[Target(id="t1")])
    verdict = session.try_commit(Item(id="i1", name="widget", linked="t1"))
    assert verdict.ok
    assert TypeFact("i1", "Item", "asserted") in session.facts
    assert session.staged_facts == ()


def test_try_commit_rolls_back_on_reject():
    session = guard.session(seed=[Target(id="t1")])
    before = session.facts
    verdict = session.try_commit(Item(id="bad", name="broken", linked="ghost"))
    assert not verdict.ok
    assert session.facts == before and session.staged_facts == ()


def test_guarded_runs_body_then_commits():
    session = guard.session(seed=[Target(id="t1")])
    effects = []
    with session.guarded(Item(id="i1", name="widget", linked="t1")) as verdict:
        assert verdict.ok
        assert TypeFact("i1", "Item", "asserted") not in session.facts  # not committed yet
        effects.append("ran")
    assert effects == ["ran"]
    assert TypeFact("i1", "Item", "asserted") in session.facts


def test_guarded_raises_before_body_on_reject():
    session = guard.session(seed=[Target(id="t1")])
    before = session.facts
    with pytest.raises(LoreViolation) as excinfo:
        with session.guarded(Item(id="bad", name="broken", linked="ghost")):
            raise AssertionError("body must not run")
    err = excinfo.value
    assert not err.verdict.ok
    assert str(err) == err.repair_prompt == err.verdict.repair_prompt()
    assert session.facts == before and session.staged_facts == ()


def test_guarded_rolls_back_when_body_raises():
    session = guard.session(seed=[Target(id="t1")])
    before = session.facts
    with pytest.raises(RuntimeError, match="ledger down"):
        with session.guarded(Item(id="i1", name="widget", linked="t1")):
            raise RuntimeError("ledger down")
    assert session.facts == before and session.staged_facts == ()


def test_guarded_forbids_session_calls_in_the_body():
    session = guard.session(seed=[Target(id="t1")])
    before = session.facts
    with pytest.raises(LoreError, match="guarded"):
        with session.guarded(Item(id="i1", name="widget", linked="t1")):
            session.propose(Item(id="i2", name="sneaky"))
    assert session.facts == before and session.staged_facts == ()


def test_guarded_yields_flag_violations_and_still_commits():
    flag_ont = Lore("flag-test")

    @flag_ont.entity
    class Note(Entity):
        kind: str = one_of("a", "b", severity="flag")

    session = flag_ont.compile().session()
    with session.guarded(Note(id="n1", kind="weird")) as verdict:
        assert verdict.ok and len(verdict.flags) == 1
    assert TypeFact("n1", "Note", "asserted") in session.facts


# --- inspection: graph / repr / dump ------------------------------------------


def test_session_graph_reads_committed_and_staged():
    session = guard.session(seed=[Target(id="t1")])
    session.try_commit(Item(id="i1", name="widget", linked="t1"))
    assert session.graph.get("i1") == Item(id="i1", name="widget", linked="t1")
    session.propose(Item(id="i2", name="draft", linked="t1"))
    assert session.graph.get("i2").name == "draft"  # staged facts are visible
    incoming = session.graph.incoming("t1", "Item.linked")
    assert {e.subject_id for e in incoming} == {"i1", "i2"}


def test_session_graph_reachable_follows_mirrors_and_closure():
    lore_hr = Lore("graph-reachable-test")

    @lore_hr.entity
    class Emp(Entity):
        boss: Relation["Emp"] | None = relation(transitive=True, irreflexive=True, default=None)
        manages: Relation["Emp"] | None = relation(inverse_of="boss", default=None)

    session = lore_hr.compile().session(
        seed=[Emp(id="ceo"), Emp(id="mgr", boss="ceo"), Emp(id="dev", boss="mgr")]
    )
    graph = session.graph
    assert graph.reachable("dev", "Emp.boss") == {"mgr", "ceo"}
    assert graph.reachable("ceo", "Emp.boss") == set()
    # Mirror edges traverse too: ceo manages mgr/dev via the inverse.
    assert graph.reachable("ceo", "Emp.manages") == {"mgr", "dev"}
    # get()/incoming() stay pinned to asserted facts — derived edges must not
    # leak into rehydration.
    assert graph.get("ceo") == Emp(id="ceo")
    assert graph.incoming("dev", "Emp.boss") == []


def test_check_leaves_no_trace_with_inference_in_play():
    lore_hr = Lore("check-trace-test")

    @lore_hr.entity
    class Emp(Entity):
        boss: Relation["Emp"] | None = relation(transitive=True, irreflexive=True, default=None)

    session = lore_hr.compile().session(seed=[Emp(id="a"), Emp(id="b", boss="a")])
    pending = session.propose(Emp(id="c", boss="b"))
    staged_before = session.staged_facts
    bad = session.check(Emp(id="a", boss="b"))  # would close the a↔b cycle
    assert [v.check for v in bad.violations] == ["irreflexive"]
    assert session.staged_facts == staged_before and session.last_verdict is pending
    session.commit()
    assert TypeFact("c", "Emp", "asserted") in session.facts
    assert session._step == 1


def test_session_repr_summarizes_state():
    session = guard.session(seed=[Target(id="t1")])
    r = repr(session)
    assert "'session-test'" in r and "1 facts / 1 entities committed" in r
    assert "0 staged" in r and "no verdict yet" in r
    session.propose(Item(id="bad", name="broken", linked="ghost"))
    assert "1 reject(s)" in repr(session)


def test_dump_groups_by_entity():
    session = guard.session(seed=[Target(id="t1")])
    session.try_commit(Item(id="i1", name="widget", linked="t1"))
    session.propose(Item(id="i2", name="draft", linked="t1"))
    text = session.dump()
    assert "committed:" in text and "staged:" in text
    assert "t1: Target (seed)" in text
    assert "i1: Item  name='widget'  linked → t1" in text
    assert "i2: Item  name='draft'  linked → t1" in text


def test_verdict_repr():
    session = guard.session(seed=[Target(id="t1")])
    assert repr(session.check(Item(id="i1", name="w", linked="t1"))) == "<Verdict: ok>"
    bad = session.check(Item(id="i2", name="b", linked="ghost"))
    assert repr(bad) == "<Verdict: 1 reject(s), 0 flag(s)>"


# --- multi-valued relations: hydration and dump ---------------------------------

many_lore = Lore("session-many-test")


@many_lore.entity
class Tag(Entity):
    pass


@many_lore.entity
class Doc(Entity):
    tags: Relation[list[Tag]]  # required: [] must still round-trip


many_guard = many_lore.compile()


def test_graph_get_returns_the_full_list_committed_and_staged():
    session = many_guard.session(seed=[Tag(id="t1"), Tag(id="t2"), Tag(id="t3")])
    assert session.try_commit(Doc(id="d1", tags=["t1", "t2"])).ok
    assert session.graph.get("d1").tags == ["t1", "t2"]
    assert session.propose(Doc(id="d1", tags=["t1", "t2", "t3"])).ok
    assert session.graph.get("d1").tags == ["t1", "t2", "t3"]  # staged edge visible


def test_empty_list_required_many_field_round_trips_through_graph_get():
    session = many_guard.session(seed=[Tag(id="t1")])
    assert session.try_commit(Doc(id="d1", tags=[])).ok
    assert session.graph.get("d1") == Doc(id="d1", tags=[])


def test_dump_shows_every_many_edge():
    session = many_guard.session(seed=[Tag(id="t1"), Tag(id="t2")])
    assert session.try_commit(Doc(id="d1", tags=["t1", "t2"])).ok
    assert "d1: Doc  tags → t1  tags → t2" in session.dump()
