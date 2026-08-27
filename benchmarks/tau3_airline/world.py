"""Lore ontology for the tau3-bench (tau2-bench v1.x) airline domain.

Deliberately axiom-forward: constraints live in the declarative layer wherever
the policy allows, with ``@lore.rule`` reserved for genuine arithmetic and
eligibility logic. The axiom inventory:

- cardinality: at most 1 certificate / 1 credit card / 3 gift cards paying for
  a reservation (``max_per_target=1/1/3``), at most 5 passengers
  (``max_per_target=5``), cancel-once (``max_per_target=1``), one
  authenticated customer per conversation (``max_per_target=1``).
- range-via-type-hierarchy: ``FlightUpdate.updates`` targets
  ``ModifiableReservation`` — a ``BasicEconomyReservation`` simply isn't one,
  so "basic economy flights cannot be modified" is a range violation, not a
  rule. Same trick makes "certificates cannot pay for updates" structural:
  update payments target ``NonCertificateMethod``.
- disjointness: basic-economy vs modifiable reservations; certificates vs
  non-certificate payment methods; credit cards vs gift cards.
- one_of: cabin, trip type, membership, insurance, reservation status,
  flight-date status.
- existence: every relation id must resolve against the seeded profile
  ("all payment methods must already be in user profile" is the built-in
  existence check).

Cabin-only changes are legal for every cabin class (including basic economy)
and arrive through the same env tool as flight changes, so the harness maps
``update_reservation_flights`` to ``CabinUpdate`` when the flight list is
unchanged and to ``FlightUpdate`` otherwise.

Escape-hatch rules (owned, per the field_validator framing): authentication,
cancellation eligibility (24h window via the seeded Clock / business cabin /
insurance / airline-cancelled), flown-segment gates, itinerary immutability,
baggage monotonicity + allowance table, passenger-count immutability,
payment ownership, compensation eligibility (reject) and amount (flag).

The current time is fixed by the domain ("2024-05-15 15:00:00 EST") and
seeded as ``Clock(id="clock")``.
"""

from datetime import datetime, timedelta

from lore import Entity, Graph, Lore, Relation, one_of, relation

lore = Lore("tau3-airline")

CONVERSATION_ID = "conversation"
CLOCK_ID = "clock"

# free checked bags per passenger: membership -> cabin -> allowance
FREE_BAGS = {
    "regular": {"basic_economy": 0, "economy": 1, "business": 2},
    "silver": {"basic_economy": 1, "economy": 2, "business": 3},
    "gold": {"basic_economy": 2, "economy": 3, "business": 4},
}


# --- seeded world state --------------------------------------------------------


@lore.entity
class Conversation(Entity):
    """Singleton session anchor; authentication hangs off it."""


@lore.entity
class Clock(Entity):
    """Fixed domain time, seeded once per session."""

    now: str  # YYYY-MM-DDTHH:MM:SS


@lore.entity
class Customer(Entity):
    membership: str = one_of("regular", "silver", "gold")
    email: str
    dob: str


@lore.entity
class PaymentMethod(Entity):
    owned_by: Relation[Customer]


@lore.entity
class NonCertificateMethod(PaymentMethod):
    """Methods that may pay for reservation updates (policy: never a
    certificate). Declared as a type so the constraint is a range check."""


@lore.entity
class CreditCard(NonCertificateMethod):
    pass


@lore.entity
class GiftCard(NonCertificateMethod):
    lore_disjoint_with = [CreditCard]
    balance: float


@lore.entity
class Certificate(PaymentMethod):
    lore_disjoint_with = [NonCertificateMethod]
    balance: float


@lore.entity
class Reservation(Entity):
    owned_by: Relation[Customer]
    origin: str
    destination: str
    trip_type: str = one_of("one_way", "round_trip")
    cabin: str = one_of("basic_economy", "economy", "business")
    created_at: str  # YYYY-MM-DDTHH:MM:SS
    insurance: str = one_of("yes", "no")
    total_baggages: int
    nonfree_baggages: int
    num_passengers: int
    status: str = one_of("active", "cancelled")


@lore.entity
class ModifiableReservation(Reservation):
    """Every non-basic-economy reservation. Flight changes range-check
    against this type."""


@lore.entity
class BasicEconomyReservation(Reservation):
    lore_disjoint_with = [ModifiableReservation]


@lore.entity
class Segment(Entity):
    """One flight leg of a reservation, with its date-specific status."""

    in_reservation: Relation[Reservation]
    flight_number: str
    date: str
    day_status: str = one_of(
        "available", "on time", "delayed", "flying", "landed", "cancelled"
    )


@lore.entity
class PassengerRecord(Entity):
    in_reservation: Relation[Reservation] = relation(max_per_target=5)
    first_name: str
    last_name: str
    dob: str


# --- payment-use entities (booking): the 1 / 1 / 3 composition axioms ----------


@lore.entity
class PaymentUse(Entity):
    """Abstract base so ownership rules cover every booking payment.
    Each subclass declares its own ``pays_for`` — the cardinality differs
    per payment kind, and lore forbids redeclaring an inherited field."""


@lore.entity
class CertificateUse(PaymentUse):
    pays_for: Relation[Reservation] = relation(max_per_target=1)
    method: Relation[Certificate]


@lore.entity
class CreditCardUse(PaymentUse):
    pays_for: Relation[Reservation] = relation(max_per_target=1)
    method: Relation[CreditCard]


@lore.entity
class GiftCardUse(PaymentUse):
    pays_for: Relation[Reservation] = relation(max_per_target=3)
    method: Relation[GiftCard]


# --- session state asserted by the harness --------------------------------------


@lore.entity
class Authentication(Entity):
    """One authenticated customer per conversation, by cardinality."""

    customer: Relation[Customer]
    in_session: Relation[Conversation] = relation(max_per_target=1)


# --- action entities (one per guarded write tool) --------------------------------


@lore.entity
class AgentAction(Entity):
    """Abstract base: shared rules (auth, cancelled-reservation gate) fire
    for every concrete action subclass."""


@lore.entity
class Booking(AgentAction):
    """book_reservation's action entity: exists so the auth/cross-user rule
    covers bookings (the booked entities themselves are not AgentActions)."""

    to_customer: Relation[Customer]


@lore.entity
class Cancellation(AgentAction):
    cancels: Relation[Reservation] = relation(max_per_target=1)


@lore.entity
class FlightUpdate(AgentAction):
    """update_reservation_flights when the flight list actually changes."""

    updates: Relation[ModifiableReservation]  # range: basic economy excluded
    payment: Relation[NonCertificateMethod]  # range: certificates excluded
    cabin: str = one_of("basic_economy", "economy", "business")
    # both itineraries are derived from leg lists by the same function, so
    # systematic derivation quirks cancel (stored fields disagree with any
    # derivation for ~4% of the db)
    new_origin: str
    new_destination: str
    new_trip_type: str = one_of("one_way", "round_trip")
    prior_origin: str
    prior_destination: str
    prior_trip_type: str = one_of("one_way", "round_trip")


@lore.entity
class UpgradeFlightUpdate(AgentAction):
    """update_reservation_flights that changes flights AND simultaneously
    upgrades the cabin away from basic economy — tau3 gold treats this as
    legal (e.g. tasks 7, 17, 22, 32), so it must not hit the
    ModifiableReservation range check."""

    updates: Relation[Reservation]
    payment: Relation[NonCertificateMethod]
    cabin: str = one_of("economy", "business")
    new_origin: str
    new_destination: str
    new_trip_type: str = one_of("one_way", "round_trip")
    prior_origin: str
    prior_destination: str
    prior_trip_type: str = one_of("one_way", "round_trip")


@lore.entity
class CabinUpdate(AgentAction):
    """update_reservation_flights with an unchanged flight list — legal for
    every cabin class, including basic economy."""

    updates: Relation[Reservation]
    payment: Relation[NonCertificateMethod]
    new_cabin: str = one_of("basic_economy", "economy", "business")


@lore.entity
class BaggageUpdate(AgentAction):
    updates: Relation[Reservation]
    payment: Relation[NonCertificateMethod]
    total_baggages: int
    nonfree_baggages: int


@lore.entity
class PassengerUpdate(AgentAction):
    updates: Relation[Reservation]
    count: int


@lore.entity
class CertificateGrant(AgentAction):
    """send_certificate — compensation."""

    to_customer: Relation[Customer]
    amount: float


# --- rule helpers -----------------------------------------------------------------

_RES_PREDICATES = (
    "Cancellation.cancels",
    "FlightUpdate.updates",
    "CabinUpdate.updates",
    "BaggageUpdate.updates",
    "PassengerUpdate.updates",
)


def _reservation_of(action: AgentAction, graph: Graph) -> "Reservation | None":
    for field in ("cancels", "updates"):
        rid = getattr(action, field, None)
        if rid is not None:
            return graph.get(rid)
    return None


def _authenticated_customer(graph: Graph) -> "str | None":
    edges = graph.incoming(CONVERSATION_ID, "Authentication.in_session")
    if not edges:
        return None
    auth = graph.get(edges[0].subject_id)
    return auth.customer if auth else None


def _segments(reservation_id: str, graph: Graph) -> list:
    return [
        graph.get(e.subject_id)
        for e in graph.incoming(reservation_id, "Segment.in_reservation")
    ]


def _any_flown(reservation_id: str, graph: Graph) -> bool:
    return any(
        s.day_status in ("flying", "landed")
        for s in _segments(reservation_id, graph)
        if s is not None
    )


def _effective_cabin(reservation, graph: Graph,
                     exclude_id: "str | None" = None) -> str:
    """Seed cabin unless a committed action changed it (cabin is mutable;
    committed lore facts are not — state evolution lives in the actions).
    ``exclude_id`` skips the action currently being checked, whose own
    staged edge must not count as already-effective state."""
    latest, cabin = -1, reservation.cabin
    for pred, field in (("CabinUpdate.updates", "new_cabin"),
                        ("FlightUpdate.updates", "cabin"),
                        ("UpgradeFlightUpdate.updates", "cabin")):
        for e in graph.incoming(reservation.id, pred):
            if e.subject_id == exclude_id:
                continue
            action = graph.get(e.subject_id)
            if action is not None and e.step > latest:
                latest, cabin = e.step, getattr(action, field)
    return cabin


def _reservations_of_customer(customer_id: str, graph: Graph) -> list:
    return [
        graph.get(e.subject_id)
        for e in graph.incoming(customer_id, "Reservation.owned_by")
    ]


# --- rules -------------------------------------------------------------------------


@lore.rule(
    message="Action {obj.id} concerns a customer who has not been "
    "authenticated in this conversation. Obtain and verify the user id "
    "first, and deny requests about any other user."
)
def action_requires_owner_auth(action: AgentAction, graph: Graph) -> bool:
    auth = _authenticated_customer(graph)
    target = getattr(action, "to_customer", None)
    if target is not None:
        return auth == target
    reservation = _reservation_of(action, graph)
    if reservation is None:
        return True  # the existence check reports the missing reservation
    return auth == reservation.owned_by


@lore.rule(
    message="Action {obj.id} targets a reservation that is already "
    "cancelled; no further action can be taken on it."
)
def no_action_on_cancelled(action: AgentAction, graph: Graph) -> bool:
    reservation = _reservation_of(action, graph)
    if reservation is None:
        return True
    if reservation.status != "active":
        return False
    prior = [e for e in graph.incoming(reservation.id, "Cancellation.cancels")
             if e.subject_id != action.id]
    return not prior


@lore.rule(
    message="Reservation {obj.cancels} has a flight segment that has "
    "already been flown; the agent cannot cancel it — transfer to a human "
    "agent instead."
)
def cancel_requires_no_flown_segment(c: Cancellation, graph: Graph) -> bool:
    return not _any_flown(c.cancels, graph)


@lore.rule(
    message="Reservation {obj.cancels} is not eligible for cancellation: "
    "it was not booked within the last 24 hours, is not business class, "
    "has no travel insurance, and no flight in it was cancelled by the "
    "airline. Deny the request."
)
def cancel_eligibility(c: Cancellation, graph: Graph) -> bool:
    r = graph.get(c.cancels)
    clock = graph.get(CLOCK_ID)
    if r is None or clock is None:
        return True
    if _effective_cabin(r, graph, exclude_id=c.id) == "business" or r.insurance == "yes":
        return True
    if any(
        s is not None and s.day_status == "cancelled"
        for s in _segments(r.id, graph)
    ):
        return True
    now = datetime.fromisoformat(clock.now)
    created = datetime.fromisoformat(r.created_at)
    return now - created <= timedelta(hours=24)


@lore.rule(
    message="Flight update {obj.id} changes the reservation's origin, "
    "destination, or trip type, which cannot be modified. Only flights, "
    "cabin, baggage, and passenger details may change."
)
def itinerary_is_immutable(u: AgentAction, graph: Graph) -> bool:
    if not isinstance(u, (FlightUpdate, UpgradeFlightUpdate)):
        return True
    return (
        u.new_origin == u.prior_origin
        and u.new_destination == u.prior_destination
        and u.new_trip_type == u.prior_trip_type
    )


@lore.rule(
    message="{obj.id} changes the cabin of a reservation with a flight "
    "segment that has already been flown; cabin cannot be changed."
)
def cabin_change_requires_unflown(a: AgentAction, graph: Graph) -> bool:
    if isinstance(a, CabinUpdate):
        new_cabin, rid = a.new_cabin, a.updates
    elif isinstance(a, (FlightUpdate, UpgradeFlightUpdate)):
        new_cabin, rid = a.cabin, a.updates
    else:
        return True
    r = graph.get(rid)
    if r is None or new_cabin == _effective_cabin(r, graph, exclude_id=a.id):
        return True
    return not _any_flown(rid, graph)


@lore.rule(
    message="Baggage update {obj.id} reduces the number of checked bags; "
    "bags can be added but not removed."
)
def baggage_never_removed(b: BaggageUpdate, graph: Graph) -> bool:
    r = graph.get(b.updates)
    if r is None:
        return True
    current = r.total_baggages
    for e in graph.incoming(r.id, "BaggageUpdate.updates"):
        prior = graph.get(e.subject_id)
        if prior is not None and prior.id != b.id:
            current = max(current, prior.total_baggages)
    return b.total_baggages >= current


@lore.rule(
    message="Baggage update {obj.id} under-charges: given the member's "
    "level and cabin, fewer bags are free than this update assumes. "
    "nonfree_baggages must be at least total minus the free allowance."
)
def baggage_allowance_respected(b: BaggageUpdate, graph: Graph) -> bool:
    r = graph.get(b.updates)
    if r is None:
        return True
    owner = graph.get(r.owned_by)
    if owner is None:
        return True
    cabin = _effective_cabin(r, graph, exclude_id=b.id)
    free = FREE_BAGS[owner.membership][cabin] * r.num_passengers
    return b.nonfree_baggages >= b.total_baggages - free


@lore.rule(
    message="Passenger update {obj.id} changes the number of passengers, "
    "which cannot be modified — not even by a human agent."
)
def passenger_count_is_fixed(p: PassengerUpdate, graph: Graph) -> bool:
    r = graph.get(p.updates)
    return r is None or p.count == r.num_passengers


@lore.rule(
    message="The payment method used by {obj.id} does not belong to the "
    "customer who owns the reservation it targets."
)
def payment_owned_by_reservation_owner(a: AgentAction, graph: Graph) -> bool:
    reservation = _reservation_of(a, graph)
    if reservation is None:
        return True
    pm_id = getattr(a, "payment", None)
    if pm_id is None:
        return True
    pm = graph.get(pm_id)
    return pm is None or pm.owned_by == reservation.owned_by


@lore.rule(
    message="Booking payment {obj.id} uses a method not owned by the "
    "customer who owns the reservation."
)
def booking_payment_owned(u: PaymentUse, graph: Graph) -> bool:
    r = graph.get(u.pays_for)
    pm = graph.get(u.method)
    if r is None or pm is None:
        return True
    return pm.owned_by == r.owned_by


@lore.rule(
    message="Compensation {obj.id} is not permitted: it requires a "
    "cancelled or delayed flight in the customer's reservations, AND the "
    "customer to be silver/gold, insured, or business cabin. Do not offer "
    "compensation."
)
def compensation_eligibility(g: CertificateGrant, graph: Graph) -> bool:
    customer = graph.get(g.to_customer)
    if customer is None:
        return True
    reservations = [r for r in _reservations_of_customer(customer.id, graph)
                    if r is not None]
    disrupted = any(
        s is not None and s.day_status in ("cancelled", "delayed")
        for r in reservations for s in _segments(r.id, graph)
    )
    if not disrupted:
        return False
    if customer.membership in ("silver", "gold"):
        return True
    return any(r.insurance == "yes" or r.cabin == "business"
               for r in reservations)


@lore.rule(
    message="Compensation {obj.id} is for a delayed (not airline-cancelled) "
    "flight, but no reservation of this customer has been changed or "
    "cancelled in this conversation; delay compensation is only permitted "
    "after re-booking or cancelling. Deny the request."
)
def delayed_compensation_requires_rebooking(g: CertificateGrant,
                                            graph: Graph) -> bool:
    customer = graph.get(g.to_customer)
    if customer is None:
        return True
    reservations = [r for r in _reservations_of_customer(customer.id, graph)
                    if r is not None]
    if any(seg is not None and seg.day_status == "cancelled"
           for r in reservations for seg in _segments(r.id, graph)):
        return True  # airline-cancelled complaints need no prior re-booking
    for r in reservations:
        for pred in ("Cancellation.cancels", "FlightUpdate.updates",
                     "UpgradeFlightUpdate.updates", "CabinUpdate.updates"):
            if graph.incoming(r.id, pred):
                return True
    return False


@lore.rule(
    message="Compensation {obj.id} of ${obj.amount} does not match the "
    "policy schedule ($100 x passengers for airline-cancelled flights, "
    "$50 x passengers for delays after re-booking/cancelling) for any of "
    "this customer's reservations. Double-check the amount and the reason.",
    severity="flag",
)
def compensation_amount_on_schedule(g: CertificateGrant, graph: Graph) -> bool:
    customer = graph.get(g.to_customer)
    if customer is None:
        return True
    valid_amounts = set()
    for r in _reservations_of_customer(customer.id, graph):
        if r is None:
            continue
        valid_amounts.add(100.0 * r.num_passengers)
        valid_amounts.add(50.0 * r.num_passengers)
    return not valid_amounts or g.amount in valid_amounts


guard = lore.compile()
