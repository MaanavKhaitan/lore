"""derive() unit tests on hand-built stores: mirrors, closure, provenance
paths, first-path-wins, self-edge emission."""

from lore import Entity, Lore, Relation, relation
from lore.infer import derive
from lore.store import EdgeFact, InMemoryStore

lore = Lore("infer-test")


@lore.entity
class Person(Entity):
    knows: Relation["Person"] | None = relation(symmetric=True, default=None)
    related: Relation["Person"] | None = relation(symmetric=True, transitive=True, default=None)
    reports_to: Relation["Person"] | None = relation(transitive=True, default=None)
    manages: Relation["Person"] | None = relation(inverse_of="reports_to", default=None)
    mentors: Relation["Person"] | None = relation(default=None)
    mentored_by: Relation["Person"] | None = relation(inverse_of="mentors", default=None)


guard = lore.compile()

KNOWS = "Person.knows"
RELATED = "Person.related"
REPORTS = "Person.reports_to"
MANAGES = "Person.manages"
MENTORS = "Person.mentors"
MENTORED = "Person.mentored_by"


def store(*edges: EdgeFact) -> InMemoryStore:
    s = InMemoryStore()
    s.add(edges)
    return s


def keys(fact_store: InMemoryStore, predicate: str) -> set[tuple[str, str]]:
    return {(e.subject_id, e.object_id) for e in fact_store.edges(predicate)}


def test_symmetric_mirror_with_provenance():
    base = EdgeFact("a", KNOWS, "b", "asserted", step=2)
    d = derive(guard, store(base))
    assert keys(d.sym_mirrors, KNOWS) == {("b", "a")}
    derivation = d.provenance[("b", KNOWS, "a")]
    assert derivation.kind == "symmetric"
    assert derivation.base_path == (base,)
    assert derivation.edge.source == "derived" and derivation.edge.step == 2


def test_symmetric_mirror_skipped_when_both_directions_asserted():
    d = derive(
        guard,
        store(EdgeFact("a", KNOWS, "b", "asserted"), EdgeFact("b", KNOWS, "a", "asserted")),
    )
    assert keys(d.sym_mirrors, KNOWS) == set()
    assert d.provenance == {}


def test_inverse_mirrors_flip_onto_the_paired_predicate_both_directions():
    d = derive(
        guard,
        store(
            EdgeFact("a", MENTORS, "b", "asserted"),
            EdgeFact("c", MENTORED, "d", "asserted"),
        ),
    )
    assert keys(d.inv_mirrors, MENTORED) == {("b", "a")}
    assert keys(d.inv_mirrors, MENTORS) == {("d", "c")}
    assert d.provenance[("b", MENTORED, "a")].kind == "inverse"


def test_transitive_closure_paths_and_steps():
    ab = EdgeFact("a", REPORTS, "b", "seed", step=0)
    bc = EdgeFact("b", REPORTS, "c", "asserted", step=1)
    cd = EdgeFact("c", REPORTS, "d", "asserted", step=3)
    d = derive(guard, store(ab, bc, cd))
    assert keys(d.closure, REPORTS) == {("a", "c"), ("b", "d"), ("a", "d")}
    ad = d.provenance[("a", REPORTS, "d")]
    assert ad.kind == "transitive"
    assert ad.base_path == (ab, bc, cd)
    assert ad.edge.step == 3  # the step at which the derived edge became true
    assert d.provenance[("a", REPORTS, "c")].base_path == (ab, bc)


def test_first_shortest_path_wins():
    d = derive(
        guard,
        store(
            EdgeFact("a", REPORTS, "b", "asserted"),
            EdgeFact("b", REPORTS, "c", "asserted"),
            EdgeFact("c", REPORTS, "d", "asserted"),
            EdgeFact("b", REPORTS, "d", "asserted"),  # shortcut
        ),
    )
    assert len(d.provenance[("a", REPORTS, "d")].base_path) == 2


def test_cycle_emits_self_edges_with_full_cycle_paths():
    ab = EdgeFact("a", REPORTS, "b", "asserted", step=1)
    ba = EdgeFact("b", REPORTS, "a", "asserted", step=2)
    d = derive(guard, store(ab, ba))
    assert keys(d.closure, REPORTS) == {("a", "a"), ("b", "b")}
    assert d.provenance[("a", REPORTS, "a")].base_path == (ab, ba)
    assert d.provenance[("b", REPORTS, "b")].base_path == (ba, ab)


def test_closure_runs_over_inverse_mirrors_on_the_transitive_predicate():
    # "c manages b" contributes the edge b→c to reports_to's closure universe.
    ab = EdgeFact("a", REPORTS, "b", "asserted")
    cb = EdgeFact("c", MANAGES, "b", "asserted")
    d = derive(guard, store(ab, cb))
    assert keys(d.closure, REPORTS) == {("a", "c")}
    assert d.provenance[("a", REPORTS, "c")].base_path == (ab, cb)  # base facts only


def test_closure_runs_over_symmetric_mirrors():
    ab = EdgeFact("a", RELATED, "b", "asserted")
    cb = EdgeFact("c", RELATED, "b", "asserted")
    d = derive(guard, store(ab, cb))
    # b→a and b→c are mirrors; a→c and c→a arrive via them.
    assert ("a", "c") in keys(d.closure, RELATED)
    assert ("c", "a") in keys(d.closure, RELATED)
    assert d.provenance[("a", RELATED, "c")].base_path == (ab, cb)


def test_no_derivations_without_characteristics():
    d = derive(guard, store(EdgeFact("a", MENTORS, "b", "asserted")))
    assert keys(d.closure, MENTORS) == set()
    assert keys(d.sym_mirrors, MENTORS) == set()
