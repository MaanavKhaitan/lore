"""Multi-valued relations: grounding, accretion, rules, and inference."""

from lore import Entity, Lore, Relation, relation
from lore.engine import ground
from lore.store import EdgeFact


# --- multi-valued relations (Relation[list[X]]): grounding, per-edge checks,
# accretion. Facts are append-only, so a many field accretes like a set —
# re-proposing an id whose list grew stages only the new edges, and
# single_value (scalar immutability) deliberately does not apply.

contracts = Lore("many-relation-test")


@contracts.entity
class DefinedTerm(Entity):
    pass


@contracts.entity
class Clause(Entity):
    # Required (no default): an empty list must be constructible and hydrate back.
    uses_terms: Relation[list[DefinedTerm]] = relation(max_per_target=2)


@contracts.rule(message="Clause {obj.id} uses more than three defined terms.")
def at_most_three_terms(clause: Clause, graph) -> bool:
    return len(clause.uses_terms) <= 3


many_guard = contracts.compile()


def many_session():
    return many_guard.session(seed=[DefinedTerm(id=f"t_{i}") for i in range(1, 6)])


def test_ground_emits_one_edge_per_distinct_list_element():
    facts = ground(many_guard, Clause(id="c_1", uses_terms=["t_1", "t_2", "t_1"]), "asserted")
    edges = [f for f in facts if isinstance(f, EdgeFact)]
    assert [(e.subject_id, e.predicate, e.object_id) for e in edges] == [
        ("c_1", "Clause.uses_terms", "t_1"),
        ("c_1", "Clause.uses_terms", "t_2"),
    ]


def test_ground_empty_list_emits_zero_edges():
    facts = ground(many_guard, Clause(id="c_1", uses_terms=[]), "asserted")
    assert not any(isinstance(f, EdgeFact) for f in facts)


def test_existence_fires_once_per_dangling_element():
    verdict = many_session().propose(Clause(id="c_1", uses_terms=["t_1", "ghost_a", "ghost_b"]))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("existence", ("c_1", "ghost_a")),
        ("existence", ("c_1", "ghost_b")),
    }


def test_max_per_target_counts_many_edges():
    verdict = many_session().propose(
        Clause(id="c_1", uses_terms=["t_1"]),
        Clause(id="c_2", uses_terms=["t_1"]),
        Clause(id="c_3", uses_terms=["t_1"]),
    )
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("max_per_target", ("t_1", "c_1", "c_2", "c_3"))
    }


def test_single_value_stays_silent_on_multi_edge_many_subjects():
    verdict = many_session().propose(Clause(id="c_1", uses_terms=["t_1", "t_2", "t_3"]))
    assert verdict.ok and verdict.violations == []


def test_accretion_reproposal_stages_only_the_new_edges_and_commits():
    session = many_session()
    assert session.try_commit(Clause(id="c_1", uses_terms=["t_1", "t_2"])).ok
    verdict = session.propose(Clause(id="c_1", uses_terms=["t_1", "t_2", "t_3"]))
    assert verdict.ok
    assert session.staged_facts == (
        EdgeFact("c_1", "Clause.uses_terms", "t_3", "asserted"),
    )
    session.commit()
    assert session.graph.get("c_1").uses_terms == ["t_1", "t_2", "t_3"]


def test_accretion_never_removes_a_committed_reference():
    session = many_session()
    assert session.try_commit(Clause(id="c_1", uses_terms=["t_1", "t_2"])).ok
    assert session.try_commit(Clause(id="c_1", uses_terms=["t_1"])).ok  # subset: no new facts
    assert session.graph.get("c_1").uses_terms == ["t_1", "t_2"]


def test_rule_receives_the_full_accreted_list():
    session = many_session()
    assert session.try_commit(Clause(id="c_1", uses_terms=["t_1", "t_2", "t_3"])).ok
    # The staged edge alone is fine; the rule must see committed ∪ staged.
    verdict = session.propose(Clause(id="c_1", uses_terms=["t_1", "t_2", "t_3", "t_4"]))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("rule:at_most_three_terms", ("c_1",))
    }


# --- multi-valued relations meet inference: edges are edges, so the
# characteristics and inverse pairing work unchanged per element.

tree = Lore("many-tree-test")


@tree.entity
class Section(Entity):
    contains: Relation[list["Section"]] = relation(transitive=True, irreflexive=True, default=[])


tree_guard = tree.compile()


def tree_session():
    session = tree_guard.session(
        seed=[Section(id="root"), Section(id="a"), Section(id="b"), Section(id="c")]
    )
    verdict = session.try_commit(
        Section(id="root", contains=["a", "b"]), Section(id="a", contains=["c"])
    )
    assert verdict.ok, f"tree setup failed: {verdict.violations}"
    return session


def test_transitive_many_reachable_over_branches():
    assert tree_session().graph.reachable("root", "Section.contains") == {"a", "b", "c"}


def test_transitive_many_cycle_rejected_with_rendered_chain():
    verdict = tree_session().propose(Section(id="c", contains=["root"]))
    (violation,) = verdict.violations
    assert violation.message == (
        "root cannot reach itself via 'contains', but this proposal creates a "
        "cycle: root → a (already committed at step 1) → c (already committed at "
        "step 1) → root (proposed)."
    )


def test_symmetric_many_mirrors_without_single_value():
    pals = Lore("many-symmetric-test")

    @pals.entity
    class Person(Entity):
        friends: Relation[list["Person"]] = relation(symmetric=True, default=[])

    session = pals.compile().session(
        seed=[Person(id="p_1"), Person(id="p_2"), Person(id="p_3")]
    )
    assert session.try_commit(Person(id="p_1", friends=["p_2"])).ok
    # p_2 ends up befriending both p_1 and p_3, each fact asserted from the
    # other side — visible only through symmetric mirrors, and fine on a many
    # field (the scalar equivalent fires single_value).
    verdict = session.try_commit(Person(id="p_3", friends=["p_2"]))
    assert verdict.ok and verdict.violations == []
    assert session.graph.reachable("p_2", "Person.friends") == {"p_1", "p_2", "p_3"}


squads = Lore("many-inverse-test")


@squads.entity
class SquadMember(Entity):
    member_of: Relation["Squad"] | None = relation(default=None)


@squads.entity
class Squad(Entity):
    # One-to-many pair: a member belongs to at most one squad, enforced
    # whichever direction asserts the fact.
    members: Relation[list[SquadMember]] = relation(
        inverse_of="member_of", max_per_target=1, default=[]
    )


squads_guard = squads.compile()


def test_inverse_pairing_many_with_scalar_enforces_max_per_target_cross_direction():
    session = squads_guard.session(
        seed=[SquadMember(id="m_1"), SquadMember(id="m_2"), Squad(id="s_1"), Squad(id="s_2")]
    )
    assert session.try_commit(Squad(id="s_1", members=["m_1", "m_2"])).ok
    verdict = session.propose(SquadMember(id="m_1", member_of="s_2"))
    assert {(v.check, v.subjects) for v in verdict.violations} == {
        ("max_per_target", ("m_1", "s_1", "s_2"))
    }
    (violation,) = verdict.violations
    assert "m_1 has 2: s_1 (already committed at step 1), s_2 (proposed)" in violation.message
    # Re-asserting a committed membership from the scalar side is redundant, not
    # a violation: the inverse mirror duplicates the asserted edge and is dropped.
    session.rollback()
    assert session.try_commit(SquadMember(id="m_1", member_of="s_1")).ok
