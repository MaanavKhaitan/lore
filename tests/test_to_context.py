"""to_context(): the ontology rendered as system-prompt English."""

from pathlib import Path

import ontic
from ontic import Entity, Graph, Ontology, Relation, one_of, relation

ont = Ontology("commerce")


@ont.entity
class Customer(Entity):
    name: str


@ont.entity
class VIPCustomer(Customer):
    tier: str


@ont.entity
class SupportRep(Entity):
    ontic_disjoint_with = [Customer]
    name: str


@ont.entity
class Order(Entity):
    status: str = one_of("paid", "shipped")
    placed_by: Relation[Customer]


@ont.entity
class Refund(Entity):
    refunds: Relation[Order] = relation(max_per_target=1)


@ont.rule(message="Refund {obj.id} exceeds the total of order {obj.refunds}.")
def within_total(refund: Refund, graph: Graph) -> bool:
    return True


guard = ont.compile()


def test_to_context_renders_entities_and_rules():
    text = guard.to_context()
    assert "domain 'commerce'" in text
    assert "- Customer: id, name" in text
    assert "- VIPCustomer (a kind of Customer): id, tier" in text  # inherited fields not repeated
    assert "- Order: id, status (one of 'paid', 'shipped'), placed_by -> Customer id" in text
    assert "- Nothing can be both a Customer and a SupportRep." in text
    assert "- An Order can have at most 1 Refund pointing at it via 'refunds'." in text
    assert "- Every relation field must reference an entity that exists." in text
    assert "- Never: Refund {id} exceeds the total of order {refunds}." in text


def test_to_context_is_deterministic_and_reachable_from_the_ontology():
    assert guard.to_context() == guard.to_context() == ont.to_context()


def test_py_typed_marker_ships_with_the_package():
    assert (Path(ontic.__file__).parent / "py.typed").is_file()
