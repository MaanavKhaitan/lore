"""Registration, forward-ref resolution, and every compile-time self-check."""

import pytest

from ontic import Entity, Ontology, OntologyError, Relation, one_of, relation


def test_entity_registration_returns_class_and_registers():
    ont = Ontology("t")

    @ont.entity
    class Customer(Entity):
        pass

    assert ont._classes == {"Customer": Customer}


def test_entity_rejects_non_entity_class():
    ont = Ontology("t")
    with pytest.raises(OntologyError, match="subclass of ontic.Entity"):

        @ont.entity
        class NotAnEntity:
            pass


def test_entity_rejects_duplicate_class_name():
    ont = Ontology("t")

    @ont.entity
    class Customer(Entity):
        pass

    first = Customer

    with pytest.raises(OntologyError, match="already registered"):

        @ont.entity
        class Customer(Entity):  # noqa: F811 - the collision is the point
            pass

    assert ont._classes["Customer"] is first


def test_forward_reference_target_resolves():
    ont = Ontology("t")

    @ont.entity
    class Order(Entity):
        region: Relation["Region"]

    @ont.entity
    class Region(Entity):
        pass

    guard = ont.compile()
    assert guard.relations["Order.region"].target == "Region"


def test_unknown_forward_reference_raises():
    ont = Ontology("t")

    @ont.entity
    class Order(Entity):
        region: Relation["Nowhere"]

    with pytest.raises(OntologyError, match="'Nowhere' is not a registered entity"):
        ont.compile()


def test_unregistered_target_class_raises():
    class Unregistered(Entity):
        pass

    ont = Ontology("t")

    @ont.entity
    class Order(Entity):
        target: Relation[Unregistered]

    with pytest.raises(OntologyError, match="'Unregistered' is not registered"):
        ont.compile()


def test_disjoint_with_unregistered_class_raises():
    ont = Ontology("t")

    @ont.entity
    class Rep(Entity):
        ontic_disjoint_with = ["Customer"]

    with pytest.raises(OntologyError, match="'Customer', which is not a registered"):
        ont.compile()


def test_disjoint_with_own_ancestor_raises():
    ont = Ontology("t")

    @ont.entity
    class Customer(Entity):
        pass

    @ont.entity
    class VIPCustomer(Customer):
        ontic_disjoint_with = [Customer]

    with pytest.raises(OntologyError, match="subclasses the other"):
        ont.compile()


def test_one_of_with_zero_values_raises():
    ont = Ontology("t")

    @ont.entity
    class Order(Entity):
        status: str = one_of()

    with pytest.raises(OntologyError, match="at least one allowed value"):
        ont.compile()


def test_max_per_target_below_one_raises():
    ont = Ontology("t")

    @ont.entity
    class Order(Entity):
        pass

    @ont.entity
    class Refund(Entity):
        refunds: Relation[Order] = relation(max_per_target=0)

    with pytest.raises(OntologyError, match="max_per_target must be an int >= 1"):
        ont.compile()


def test_relation_options_on_plain_field_raise():
    ont = Ontology("t")

    @ont.entity
    class Order(Entity):
        status: str = relation(max_per_target=1)

    with pytest.raises(OntologyError, match="without a Relation"):
        ont.compile()


def test_one_of_on_relation_field_raises():
    ont = Ontology("t")

    @ont.entity
    class Order(Entity):
        pass

    @ont.entity
    class Refund(Entity):
        refunds: Relation[Order] = one_of("a", "b")

    with pytest.raises(OntologyError, match="one_of\\(\\) cannot be used on a relation"):
        ont.compile()


def test_rule_requires_two_parameters():
    ont = Ontology("t")

    @ont.entity
    class Order(Entity):
        pass

    with pytest.raises(OntologyError, match="exactly \\(obj, graph\\)"):

        @ont.rule(message="nope")
        def bad_rule(order: Order):
            return True


def test_rule_requires_annotated_first_parameter():
    ont = Ontology("t")

    with pytest.raises(OntologyError, match="needs a type annotation"):

        @ont.rule(message="nope")
        def bad_rule(order, graph):
            return True


def test_rule_with_unregistered_target_raises():
    class Unregistered(Entity):
        pass

    ont = Ontology("t")

    @ont.rule(message="nope")
    def bad_rule(obj: Unregistered, graph):
        return True

    with pytest.raises(OntologyError, match="not a registered entity"):
        ont.compile()


def test_mro_ancestors_for_registered_subclass():
    ont = Ontology("t")

    @ont.entity
    class Customer(Entity):
        pass

    @ont.entity
    class VIPCustomer(Customer):
        pass

    guard = ont.compile()
    assert guard.classes["VIPCustomer"].ancestors == ("VIPCustomer", "Customer")
    assert guard.classes["Customer"].ancestors == ("Customer",)


def test_inherited_field_keeps_defining_class_predicate():
    ont = Ontology("t")

    @ont.entity
    class Order(Entity):
        pass

    @ont.entity
    class Refund(Entity):
        refunds: Relation[Order] = relation(max_per_target=1)

    @ont.entity
    class VIPRefund(Refund):
        pass

    guard = ont.compile()
    # A VIPRefund edge counts against the same "Refund.refunds" cardinality pool.
    assert guard.classes["VIPRefund"].relations["refunds"].predicate == "Refund.refunds"
    assert set(guard.relations) == {"Refund.refunds"}
