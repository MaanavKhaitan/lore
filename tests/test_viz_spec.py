"""spec_json: the World-tab payload — structure, order, English mirrors."""

import json

from lore import Entity, Graph, Lore, Relation, one_of, relation
from lore.viz import SPEC_FORMAT, spec_json

lore = Lore("viz-spec-test")


@lore.entity
class Customer(Entity):
    """Someone who buys things."""

    name: str


@lore.entity
class Vip(Customer):
    tier: str = one_of("gold", "platinum")


@lore.entity
class SupportRep(Entity):
    lore_disjoint_with = [Customer]
    name: str


@lore.entity
class Order(Entity):
    status: str = one_of("paid", "shipped")
    note: str | None = None
    placed_by: Relation[Customer]


@lore.entity
class Refund(Entity):
    amount: float
    refunds: Relation[Order] = relation(max_per_target=1, severity="flag")


@lore.entity
class Employee(Entity):
    name: str
    reports_to: Relation["Employee"] | None = relation(
        transitive=True, irreflexive=True, default=None
    )
    mentors: Relation["Employee"] | None = relation(asymmetric=True, default=None)


@lore.entity
class Invoice(Entity):
    settled_by: Relation["Payment"] | None = relation(default=None)


@lore.entity
class Payment(Entity):
    reconciles: Relation[Invoice] | None = relation(
        inverse_of="settled_by", max_per_target=1, default=None
    )


@lore.entity
class Basket(Entity):
    orders: Relation[list[Order]] = relation(default=[])


@lore.rule(message="Refund {obj.id} exceeds the total.", severity="flag")
def refund_within_total(refund: Refund, graph: Graph) -> bool:
    """The refund can never exceed what was paid."""
    return True


@lore.goal(message="Order {obj.id} has no refund.")
def order_refunded(order: Order, graph: Graph) -> bool:
    return True


guard = lore.compile()
spec = spec_json(guard)


def test_envelope():
    assert spec["format"] == SPEC_FORMAT
    assert spec["kind"] == "lore-spec"
    assert spec["lore"] == "viz-spec-test"
    assert spec["fingerprint"] == guard.fingerprint


def test_classes_in_declaration_order_with_parents_and_doc():
    names = [c["name"] for c in spec["classes"]]
    assert names == [
        "Customer",
        "Vip",
        "SupportRep",
        "Order",
        "Refund",
        "Employee",
        "Invoice",
        "Payment",
        "Basket",
    ]
    by_name = {c["name"]: c for c in spec["classes"]}
    assert by_name["Vip"]["parents"] == ["Customer"]
    assert by_name["Customer"]["parents"] == []
    assert by_name["Customer"]["doc"] == "Someone who buys things."
    assert by_name["Vip"]["doc"] is None  # not inherited from Customer


def test_inherited_field_is_marked():
    vip_fields = {f["name"]: f for c in spec["classes"] if c["name"] == "Vip" for f in c["fields"]}
    assert vip_fields["name"]["inheritedFrom"] == "Customer"
    assert vip_fields["tier"]["inheritedFrom"] is None
    assert vip_fields["tier"]["oneOf"] == ["gold", "platinum"]


def test_attr_fields_render_types_and_defaults():
    order_fields = {
        f["name"]: f for c in spec["classes"] if c["name"] == "Order" for f in c["fields"]
    }
    assert order_fields["status"]["type"] == "str"
    assert order_fields["note"]["type"] == "str | None"
    assert order_fields["note"]["required"] is False
    assert order_fields["note"]["default"] is None  # None defaults stay null
    assert order_fields["status"]["required"] is True
    assert order_fields["placed_by"] == {
        "kind": "relation",
        "name": "placed_by",
        "predicate": "Order.placed_by",
        "target": "Customer",
        "many": False,
        "required": True,
        "inheritedFrom": None,
    }


def test_relation_characteristics_and_implied_irreflexive():
    relations = {r["predicate"]: r for r in spec["relations"]}
    reports = relations["Employee.reports_to"]
    assert reports["transitive"] and reports["irreflexive"] and not reports["symmetric"]
    mentors = relations["Employee.mentors"]
    assert mentors["asymmetric"] and mentors["irreflexive"]  # implied at compile
    refunds = relations["Refund.refunds"]
    assert refunds["maxPerTarget"] == 1 and refunds["severity"] == "flag"
    assert relations["Basket.orders"]["many"] is True
    assert relations["Payment.reconciles"]["inverse"] == "Invoice.settled_by"
    assert relations["Invoice.settled_by"]["inverse"] == "Payment.reconciles"


def test_inverse_pairs_deduped_and_disjoint_pairs():
    assert spec["inversePairs"] == [["Invoice.settled_by", "Payment.reconciles"]]
    assert ["Customer", "SupportRep"] in spec["disjointPairs"]


def test_rules_and_goals_mirror_to_context_phrasing():
    (rule,) = spec["rules"]
    assert rule["name"] == "refund_within_total"
    assert rule["target"] == "Refund"
    assert rule["doc"] == "The refund can never exceed what was paid."
    assert rule["plain"] == (
        "Never: Refund {id} exceeds the total. (advisory: flagged, not rejected)"
    )
    (goal,) = spec["goals"]
    assert goal["plain"] == "Never finish while: Order {id} has no refund."
    assert goal["doc"] is None


def test_payload_is_json_safe_and_deterministic():
    dumped = json.dumps(spec)
    assert json.loads(dumped) == spec
    assert json.dumps(spec_json(guard)) == dumped
