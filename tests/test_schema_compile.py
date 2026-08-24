"""Registration, forward-ref resolution, and every compile-time self-check."""

import pytest

from lore import Entity, Lore, LoreError, Relation, one_of, relation


def test_entity_registration_returns_class_and_registers():
    lore = Lore("t")

    @lore.entity
    class Customer(Entity):
        pass

    assert lore._classes == {"Customer": Customer}


def test_entity_rejects_non_entity_class():
    lore = Lore("t")
    with pytest.raises(LoreError, match="subclass of lore.Entity"):

        @lore.entity
        class NotAnEntity:
            pass


def test_entity_rejects_duplicate_class_name():
    lore = Lore("t")

    @lore.entity
    class Customer(Entity):
        pass

    first = Customer

    with pytest.raises(LoreError, match="already registered"):

        @lore.entity
        class Customer(Entity):  # noqa: F811 - the collision is the point
            pass

    assert lore._classes["Customer"] is first


def test_forward_reference_target_resolves():
    lore = Lore("t")

    @lore.entity
    class Order(Entity):
        region: Relation["Region"]

    @lore.entity
    class Region(Entity):
        pass

    guard = lore.compile()
    assert guard.relations["Order.region"].target == "Region"


def test_unknown_forward_reference_raises():
    lore = Lore("t")

    @lore.entity
    class Order(Entity):
        region: Relation["Nowhere"]

    with pytest.raises(LoreError, match="'Nowhere' is not a registered entity"):
        lore.compile()


def test_unregistered_target_class_raises():
    class Unregistered(Entity):
        pass

    lore = Lore("t")

    @lore.entity
    class Order(Entity):
        target: Relation[Unregistered]

    with pytest.raises(LoreError, match="'Unregistered' is not registered"):
        lore.compile()


def test_disjoint_with_unregistered_class_raises():
    lore = Lore("t")

    @lore.entity
    class Rep(Entity):
        lore_disjoint_with = ["Customer"]

    with pytest.raises(LoreError, match="'Customer', which is not a registered"):
        lore.compile()


def test_disjoint_with_own_ancestor_raises():
    lore = Lore("t")

    @lore.entity
    class Customer(Entity):
        pass

    @lore.entity
    class VIPCustomer(Customer):
        lore_disjoint_with = [Customer]

    with pytest.raises(LoreError, match="subclasses the other"):
        lore.compile()


def test_one_of_with_zero_values_raises():
    lore = Lore("t")

    @lore.entity
    class Order(Entity):
        status: str = one_of()

    with pytest.raises(LoreError, match="at least one allowed value"):
        lore.compile()


def test_max_per_target_below_one_raises():
    lore = Lore("t")

    @lore.entity
    class Order(Entity):
        pass

    @lore.entity
    class Refund(Entity):
        refunds: Relation[Order] = relation(max_per_target=0)

    with pytest.raises(LoreError, match="max_per_target must be an int >= 1"):
        lore.compile()


def test_relation_options_on_plain_field_raise():
    lore = Lore("t")

    @lore.entity
    class Order(Entity):
        status: str = relation(max_per_target=1)

    with pytest.raises(LoreError, match="without a Relation"):
        lore.compile()


def test_one_of_on_relation_field_raises():
    lore = Lore("t")

    @lore.entity
    class Order(Entity):
        pass

    @lore.entity
    class Refund(Entity):
        refunds: Relation[Order] = one_of("a", "b")

    with pytest.raises(LoreError, match="one_of\\(\\) cannot be used on a relation"):
        lore.compile()


def test_rule_requires_two_parameters():
    lore = Lore("t")

    @lore.entity
    class Order(Entity):
        pass

    with pytest.raises(LoreError, match="exactly \\(obj, graph\\)"):

        @lore.rule(message="nope")
        def bad_rule(order: Order):
            return True


def test_rule_requires_annotated_first_parameter():
    lore = Lore("t")

    with pytest.raises(LoreError, match="needs a type annotation"):

        @lore.rule(message="nope")
        def bad_rule(order, graph):
            return True


def test_rule_with_unregistered_target_raises():
    class Unregistered(Entity):
        pass

    lore = Lore("t")

    @lore.rule(message="nope")
    def bad_rule(obj: Unregistered, graph):
        return True

    with pytest.raises(LoreError, match="not a registered entity"):
        lore.compile()


def test_mro_ancestors_for_registered_subclass():
    lore = Lore("t")

    @lore.entity
    class Customer(Entity):
        pass

    @lore.entity
    class VIPCustomer(Customer):
        pass

    guard = lore.compile()
    assert guard.classes["VIPCustomer"].ancestors == ("VIPCustomer", "Customer")
    assert guard.classes["Customer"].ancestors == ("Customer",)


def test_inherited_field_keeps_defining_class_predicate():
    lore = Lore("t")

    @lore.entity
    class Order(Entity):
        pass

    @lore.entity
    class Refund(Entity):
        refunds: Relation[Order] = relation(max_per_target=1)

    @lore.entity
    class VIPRefund(Refund):
        pass

    guard = lore.compile()
    # A VIPRefund edge counts against the same "Refund.refunds" cardinality pool.
    assert guard.classes["VIPRefund"].relations["refunds"].predicate == "Refund.refunds"
    assert set(guard.relations) == {"Refund.refunds"}
