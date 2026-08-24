"""The canonical commerce ontology: customers, orders, refunds — and the axioms
an agent must not violate.
"""

from ontic import Entity, Ontology, Relation, one_of, relation

ont = Ontology("commerce")


@ont.entity
class Customer(Entity):
    name: str


@ont.entity
class SupportRep(Entity):
    # A payout recipient can never be a support-rep account: an id typed as
    # both Customer and SupportRep is rejected outright.
    ontic_disjoint_with = [Customer]
    name: str


@ont.entity
class Order(Entity):
    status: str = one_of("paid", "shipped", "refunded")
    total: float
    placed_by: Relation[Customer]


@ont.entity
class Refund(Entity):
    amount: float
    # max_per_target=1: a given Order may be pointed to by at most one Refund —
    # "an order can be refunded at most once", enforced across turns.
    refunds: Relation[Order] = relation(max_per_target=1)
    paid_to: Relation[Customer]


@ont.rule(message="Refund {obj.id} of ${obj.amount} exceeds the total of order {obj.refunds}.")
def refund_within_order_total(refund: Refund, graph) -> bool:
    """Arbitrary-Python escape hatch: cross-object arithmetic via graph.get()."""
    order = graph.get(refund.refunds)
    return order is None or refund.amount <= order.total


guard = ont.compile()
