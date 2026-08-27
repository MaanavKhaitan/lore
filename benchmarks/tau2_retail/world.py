"""Draft lore ontology for the tau2-bench retail domain.

Modeling approach: guarded tool calls are proposed as *action entities*
(Cancellation, Exchange, ...) against a session seeded with the task's slice
of db.json (Customer, PaymentMethod, Order) plus one Conversation singleton.
Committed facts are immutable, so state evolution lives in the action
entities: an order's *effective* status is its seed status plus whatever
committed actions point at it; rules traverse ``graph.incoming`` to read that
history.

Each concrete action declares its own relation field (a lore constraint: a
subclass may not redeclare an inherited field with new options, and the
"once per order" cardinality differs per action type). Rules that apply to
every action target the fieldless ``OrderAction`` base and locate the order
via ``_order_of``.

Clause-by-clause coverage vs the written retail policy, and which clauses the
tau2 environment does or does not enforce itself, is in ANALYSIS.md.
"""

from lore import Entity, Graph, Lore, Relation, one_of, relation

lore = Lore("tau2-retail")

# The harness seeds exactly one Conversation with this id per session.
CONVERSATION_ID = "conversation"

# predicate names for every concrete action's order relation
_ORDER_PREDICATES = (
    "Cancellation.cancels",
    "Exchange.exchanges",
    "Return.returns",
    "ItemsModification.modifies",
    "AddressModification.modifies",
    "PaymentModification.modifies",
)


# --- seeded world state (from the task's db.json slice) ----------------------


@lore.entity
class Conversation(Entity):
    """Singleton session anchor; authentication hangs off it."""


@lore.entity
class Customer(Entity):
    first_name: str
    last_name: str
    email: str
    zip: str


@lore.entity
class PaymentMethod(Entity):
    kind: str = one_of("credit_card", "paypal", "gift_card")
    balance: float = 0.0  # meaningful for gift cards only
    owned_by: Relation[Customer]


@lore.entity
class Order(Entity):
    # Seed status; effective status also reflects committed action entities.
    status: str = one_of("pending", "processed", "delivered", "cancelled")
    placed_by: Relation[Customer]
    payment_method: Relation[PaymentMethod]
    total: float


# --- session state asserted by the harness ------------------------------------


@lore.entity
class Authentication(Entity):
    """Asserted when the agent authenticates a user (email or name+zip).

    ``max_per_target=1`` on the conversation singleton IS the
    one-customer-per-conversation policy clause.
    """

    customer: Relation[Customer]
    in_session: Relation[Conversation] = relation(max_per_target=1)


# --- action entities (one per guarded write tool) -----------------------------


@lore.entity
class OrderAction(Entity):
    """Abstract base: rules targeting it fire for every concrete action."""


@lore.entity
class Cancellation(OrderAction):
    cancels: Relation[Order] = relation(max_per_target=1)
    reason: str = one_of("no longer needed", "ordered by mistake")


@lore.entity
class Exchange(OrderAction):
    exchanges: Relation[Order] = relation(max_per_target=1)
    payment: Relation[PaymentMethod]
    price_difference: float


@lore.entity
class Return(OrderAction):
    returns: Relation[Order] = relation(max_per_target=1)
    refund_to: Relation[PaymentMethod]


@lore.entity
class ItemsModification(OrderAction):
    modifies: Relation[Order] = relation(max_per_target=1)
    payment: Relation[PaymentMethod]
    price_difference: float


@lore.entity
class AddressModification(OrderAction):
    modifies: Relation[Order]  # address may be modified more than once


@lore.entity
class PaymentModification(OrderAction):
    modifies: Relation[Order] = relation(max_per_target=1)
    new_payment: Relation[PaymentMethod]


@lore.entity
class UserAddressModification(Entity):
    """modify_user_address targets a customer, not an order."""

    of_customer: Relation[Customer]


# --- rule helpers --------------------------------------------------------------


def _order_of(action: OrderAction, graph: Graph) -> "Order | None":
    for field in ("cancels", "exchanges", "returns", "modifies"):
        oid = getattr(action, field, None)
        if oid is not None:
            return graph.get(oid)
    return None


def _prior_actions(order_id: str, graph: Graph, *predicates: str):
    preds = predicates or _ORDER_PREDICATES
    return [e for p in preds for e in graph.incoming(order_id, p)]


def _authenticated_customer(graph: Graph) -> "str | None":
    edges = graph.incoming(CONVERSATION_ID, "Authentication.in_session")
    if not edges:
        return None
    auth = graph.get(edges[0].subject_id)
    return auth.customer if auth else None


# --- rules ----------------------------------------------------------------------


@lore.rule(
    message="Action {obj.id} targets an order whose owner has not been "
    "authenticated in this conversation. Authenticate the user first (by "
    "email, or name + zip) — even if they provided their own id — and deny "
    "requests about any other user's orders."
)
def action_requires_owner_auth(action: OrderAction, graph: Graph) -> bool:
    order = _order_of(action, graph)
    if order is None:
        return True  # the existence check reports the missing order
    return _authenticated_customer(graph) == order.placed_by


@lore.rule(
    message="{obj.id} modifies the default address of customer "
    "{obj.of_customer}, who has not been authenticated in this conversation."
)
def user_modification_requires_auth(
    m: UserAddressModification, graph: Graph
) -> bool:
    return _authenticated_customer(graph) == m.of_customer


@lore.rule(
    message="Order {obj.cancels} is not cancellable: only pending orders "
    "with no prior modifications can be cancelled."
)
def cancel_requires_clean_pending(c: Cancellation, graph: Graph) -> bool:
    order = graph.get(c.cancels)
    if order is None:
        return True
    if order.status != "pending":
        return False
    others = [
        e for e in _prior_actions(order.id, graph) if e.subject_id != c.id
    ]
    return not others


@lore.rule(
    message="{obj.id} modifies order {obj.modifies}, whose items were already "
    "modified; the policy forbids any further modification or cancellation "
    "after that. (Modify the address/payment BEFORE the items, within the "
    "same conversation.)"
)
def no_modify_after_items_modified(m: OrderAction, graph: Graph) -> bool:
    # The trap tau2's own tools miss: their pending-check is a substring
    # match, so address/payment modification after an items-modification
    # succeeds in the environment while violating the written policy.
    if not isinstance(m, (AddressModification, PaymentModification)):
        return True
    prior = graph.incoming(m.modifies, "ItemsModification.modifies")
    return all(e.subject_id == m.id for e in prior)


@lore.rule(
    message="{obj.id} requires its order to be 'delivered', but the order is "
    "not delivered."
)
def exchange_return_require_delivered(a: OrderAction, graph: Graph) -> bool:
    if not isinstance(a, (Exchange, Return)):
        return True
    order = _order_of(a, graph)
    return order is None or order.status == "delivered"


@lore.rule(
    message="{obj.id} requires its order to be 'pending', but the order is "
    "not pending."
)
def modify_requires_pending(m: OrderAction, graph: Graph) -> bool:
    if not isinstance(
        m, (ItemsModification, AddressModification, PaymentModification)
    ):
        return True
    order = _order_of(m, graph)
    return order is None or order.status == "pending"


@lore.rule(
    message="The payment method used by {obj.id} does not belong to the "
    "customer who placed the order it targets."
)
def payment_belongs_to_order_owner(a: OrderAction, graph: Graph) -> bool:
    order = _order_of(a, graph)
    if order is None:
        return True
    for field in ("payment", "refund_to", "new_payment"):
        pm_id = getattr(a, field, None)
        if pm_id is not None:
            pm = graph.get(pm_id)
            if pm is not None and pm.owned_by != order.placed_by:
                return False
    return True


@lore.rule(
    message="Refund for return {obj.id} must go to the original payment "
    "method of order {obj.returns} or to an existing gift card."
)
def return_refund_destination(r: Return, graph: Graph) -> bool:
    order = graph.get(r.returns)
    pm = graph.get(r.refund_to)
    if order is None or pm is None:
        return True
    return pm.kind == "gift_card" or pm.id == order.payment_method


@lore.rule(
    message="Gift card {obj.payment} balance cannot cover the price "
    "difference of {obj.price_difference} for {obj.id}."
)
def gift_card_covers_difference(a: Exchange, graph: Graph) -> bool:
    pm = graph.get(a.payment)
    if pm is None or pm.kind != "gift_card":
        return True
    return pm.balance >= a.price_difference


@lore.rule(
    message="Payment modification {obj.id} must switch order {obj.modifies} "
    "to a method different from its current one."
)
def payment_modification_differs(m: PaymentModification, graph: Graph) -> bool:
    order = graph.get(m.modifies)
    return order is None or m.new_payment != order.payment_method


guard = lore.compile()
