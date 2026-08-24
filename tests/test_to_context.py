"""to_context(): the lore rendered as system-prompt English."""

from pathlib import Path

import lore as lore_pkg
from lore import Entity, Graph, Lore, Relation, one_of, relation

lore = Lore("commerce")


@lore.entity
class Customer(Entity):
    name: str


@lore.entity
class VIPCustomer(Customer):
    tier: str


@lore.entity
class SupportRep(Entity):
    lore_disjoint_with = [Customer]
    name: str


@lore.entity
class Order(Entity):
    status: str = one_of("paid", "shipped")
    placed_by: Relation[Customer]


@lore.entity
class Refund(Entity):
    refunds: Relation[Order] = relation(max_per_target=1)


@lore.rule(message="Refund {obj.id} exceeds the total of order {obj.refunds}.")
def within_total(refund: Refund, graph: Graph) -> bool:
    return True


guard = lore.compile()


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


def test_to_context_is_deterministic_and_reachable_from_the_lore():
    assert guard.to_context() == guard.to_context() == lore.to_context()


def test_to_context_marks_flag_severity_constraints_as_advisory():
    flag_lore = Lore("advisory")

    @flag_lore.entity
    class Node(Entity):
        kind: str = one_of("a", "b", severity="flag")
        parent: Relation["Node"] | None = relation(max_per_target=1, severity="flag")

    text = flag_lore.to_context()
    assert "kind (one of 'a', 'b') (advisory: flagged, not rejected)" in text
    assert (
        "at most 1 Node pointing at it via 'parent'. (advisory: flagged, not rejected)" in text
    )
    assert (
        "- Every relation field must reference an entity that exists. "
        "(advisory for 'Node.parent': flagged, not rejected)" in text
    )


def test_py_typed_marker_ships_with_the_package():
    assert (Path(lore_pkg.__file__).parent / "py.typed").is_file()
