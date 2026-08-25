"""@lore.goal + session.check_goals(): compile-time validation, the
output-boundary check, repair-prompt headers, to_context rendering, the
fingerprint, and snapshot round-trips."""

import pytest

from lore import Entity, Graph, Lore, LoreError, Relation, Verdict, Violation

lore = Lore("goals-test")


@lore.entity
class Contract(Entity):
    name: str


@lore.entity
class MSAContract(Contract):
    pass


@lore.entity
class Clause(Entity):
    attached_to: Relation[Contract]


@lore.entity
class ReviewNote(Entity):
    covers: Relation[Contract]


@lore.rule(message="Clause {obj.id} is attached to nothing it can reach.")
def clause_attached(clause: Clause, graph: Graph) -> bool:
    return graph.get(clause.attached_to) is not None


@lore.goal(message="Contract {obj.id} has no clause.")
def has_clause(contract: Contract, graph: Graph) -> bool:
    return len(graph.incoming(contract.id, "Clause.attached_to")) >= 1


@lore.goal(message="Contract {obj.id} has no review note.", severity="flag")
def has_review_note(contract: Contract, graph: Graph) -> bool:
    return len(graph.incoming(contract.id, "ReviewNote.covers")) >= 1


guard = lore.compile()


def contract_session(*contract_ids: str):
    return guard.session(seed=[Contract(id=cid, name=cid) for cid in contract_ids])


# --- compile-time validation: identical contract to rules ----------------------


def test_goal_requires_two_parameters():
    l2 = Lore("t")

    @l2.entity
    class Doc(Entity):
        pass

    with pytest.raises(LoreError, match="exactly \\(obj, graph\\)"):

        @l2.goal(message="nope")
        def bad_goal(doc: Doc):
            return True


def test_goal_requires_annotated_first_parameter():
    l2 = Lore("t")

    with pytest.raises(LoreError, match="needs a type annotation"):

        @l2.goal(message="nope")
        def bad_goal(doc, graph):
            return True


def test_goal_with_unregistered_target_raises():
    class Unregistered(Entity):
        pass

    l2 = Lore("t")

    @l2.goal(message="nope")
    def bad_goal(obj: Unregistered, graph):
        return True

    with pytest.raises(LoreError, match="not a registered entity"):
        l2.compile()


def test_goal_with_unknown_forward_reference_raises():
    l2 = Lore("t")

    @l2.goal(message="nope")
    def bad_goal(obj: "Nowhere", graph):  # noqa: F821 - the dangling ref is the point
        return True

    with pytest.raises(LoreError, match="goal 'bad_goal' targets 'Nowhere'"):
        l2.compile()


def test_goals_compile_alongside_rules():
    assert [g.name for g in guard.goals] == ["has_clause", "has_review_note"]
    assert [r.name for r in guard.rules] == ["clause_attached"]
    assert guard.goals[0].target == "Contract" and guard.goals[1].severity == "flag"


# --- check_goals: the output-boundary check -------------------------------------


def test_check_goals_reports_per_instance_with_rendered_messages():
    session = contract_session("c_1", "c_2")
    assert session.try_commit(Clause(id="cl_1", attached_to="c_1")).ok
    verdict = session.check_goals()
    # c_1 has its clause; c_2 does not — only the incomplete instance fires.
    assert {(v.check, v.subjects) for v in verdict.rejects} == {("goal:has_clause", ("c_2",))}
    assert verdict.rejects[0].message == "Contract c_2 has no clause."


def test_flag_goals_surface_in_flags_without_blocking_ok():
    session = contract_session("c_1")
    assert session.try_commit(Clause(id="cl_1", attached_to="c_1")).ok
    verdict = session.check_goals()  # clause present, review note missing
    assert verdict.ok
    assert {(v.check, v.severity) for v in verdict.flags} == {("goal:has_review_note", "flag")}


def test_check_goals_all_met_is_clean():
    session = contract_session("c_1")
    assert session.try_commit(
        Clause(id="cl_1", attached_to="c_1"), ReviewNote(id="rn_1", covers="c_1")
    ).ok
    verdict = session.check_goals()
    assert verdict.ok and verdict.violations == []


def test_goal_fires_on_subclass_instances_of_its_target():
    session = guard.session(seed=[MSAContract(id="msa_1", name="MSA")])
    assert {v.check for v in session.check_goals().rejects} == {"goal:has_clause"}


def test_goal_passes_vacuously_with_zero_instances():
    session = guard.session()  # no Contract anywhere
    verdict = session.check_goals()
    assert verdict.ok and verdict.violations == []


def test_check_goals_raises_on_pending_staged_facts():
    session = contract_session("c_1")
    session.propose(Clause(id="cl_1", attached_to="c_1"))
    with pytest.raises(LoreError, match="pending proposal"):
        session.check_goals()
    session.rollback()
    assert not session.check_goals().ok  # fine at a turn boundary


def test_check_goals_leaves_session_state_untouched():
    session = contract_session("c_1")
    twin = contract_session("c_1")
    before = session.facts
    assert not session.check_goals().ok
    assert session.facts == before
    assert session.staged_facts == () and session.last_verdict is None
    # A propose after check_goals behaves exactly like one that never saw it.
    proposal = Clause(id="cl_1", attached_to="c_1")
    assert session.propose(proposal).violations == twin.propose(proposal).violations
    assert session.staged_facts == twin.staged_facts
    session.commit()
    assert session.check_goals().ok


def test_goals_never_fire_during_propose_or_seeding():
    # Seeding an instance that fails every goal must not raise (goals are not
    # part of seed validation), and proposing more entities must not run them.
    session = contract_session("c_1")
    verdict = session.propose(Contract(id="c_2", name="c_2"))
    assert verdict.ok and verdict.violations == []
    session.commit()
    assert len(session.check_goals().rejects) == 2  # the boundary still catches both


# --- repair prompt headers -------------------------------------------------------


def test_goal_only_verdict_gets_the_boundary_header():
    verdict = contract_session("c_1").check_goals()
    lines = verdict.repair_prompt().splitlines()
    assert lines[0] == "The work is not finished: it violates 1 goal(s) of this domain:"
    assert lines[1] == "  1. Contract c_1 has no clause."
    assert lines[-1] == "Complete the missing items, then finish."


def test_mixed_rule_and_goal_verdict_keeps_the_generic_header():
    verdict = Verdict(
        [
            Violation(check="goal:has_clause", severity="reject", message="incomplete"),
            Violation(check="rule:clause_attached", severity="reject", message="invalid"),
        ]
    )
    prompt = verdict.repair_prompt()
    assert prompt.startswith("Your output violates 2 rule(s) of this domain:")
    assert "Complete the missing items" not in prompt


def test_flag_only_goal_verdict_has_empty_repair_prompt():
    session = contract_session("c_1")
    assert session.try_commit(Clause(id="cl_1", attached_to="c_1")).ok
    verdict = session.check_goals()
    assert verdict.flags and verdict.repair_prompt() == ""


# --- to_context rendering --------------------------------------------------------


def test_to_context_renders_goals_after_rules_with_severity_suffix():
    text = guard.to_context()
    assert text.index("Rules:") < text.index("Goals — checked when you finish:")
    assert "- Never finish while: Contract {id} has no clause." in text
    assert (
        "- Never finish while: Contract {id} has no review note. "
        "(advisory: flagged, not rejected)" in text
    )


def test_to_context_has_no_goals_section_without_goals():
    l2 = Lore("no-goals")

    @l2.entity
    class Doc(Entity):
        pass

    assert "Goals — checked when you finish:" not in l2.to_context()


# --- the fingerprint -------------------------------------------------------------


def _fingerprint(goal_message: str | None) -> str:
    l2 = Lore("fp-goals")

    @l2.entity
    class Doc(Entity):
        pass

    if goal_message is not None:

        @l2.goal(message=goal_message)
        def doc_done(doc: Doc, graph) -> bool:
            return True

    return l2.compile().fingerprint


def test_fingerprint_changes_with_goal_registrations():
    # Goal-less lores are unaffected by the feature (the existing snapshot
    # tests prove the hash is byte-identical); declaring a goal changes it,
    # and so does any goal detail, message included.
    assert _fingerprint(None) == _fingerprint(None)
    assert _fingerprint("Doc {obj.id} is unfinished.") != _fingerprint(None)
    assert _fingerprint("Doc {obj.id} is unfinished.") != _fingerprint("Doc {obj.id} is undone.")


# --- snapshots -------------------------------------------------------------------


def test_snapshot_round_trip_agrees_with_check_goals():
    session = contract_session("c_1", "c_2")
    assert session.try_commit(Clause(id="cl_1", attached_to="c_1")).ok
    before = session.check_goals()
    restored = guard.restore(session.snapshot())
    after = restored.check_goals()
    assert after.violations == before.violations
    # Completing the restored world clears the goal there.
    assert restored.try_commit(Clause(id="cl_2", attached_to="c_2")).ok
    assert restored.check_goals().rejects == []
