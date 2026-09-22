"""Seeding and tool-call -> lore-entity mapping for the tau3 airline domain.

Two jobs:
- ``seed_from_db``: turn a tau2 ``FlightDB`` into the lore seed list.
- ``map_tool_call``: turn a write-tool call (name + args) into the lore action
  entities to propose. Booking maps to the *new entities themselves*
  (Reservation subclass + PassengerRecords + PaymentUses), so booking
  constraints are pure axioms.

Kept import-light: no tau2 imports, so the shadow replayer can reuse it.
"""

from ..world import (
    Authentication,
    Booking,
    UpgradeFlightUpdate,
    BaggageUpdate,
    BasicEconomyReservation,
    CabinUpdate,
    Cancellation,
    Certificate,
    CertificateGrant,
    CertificateUse,
    Clock,
    CLOCK_ID,
    Conversation,
    CONVERSATION_ID,
    CreditCard,
    CreditCardUse,
    Customer,
    FlightUpdate,
    GiftCard,
    GiftCardUse,
    ModifiableReservation,
    PassengerRecord,
    PassengerUpdate,
    Segment,
)

DOMAIN_NOW = "2024-05-15T15:00:00"  # fixed by the airline policy

WRITE_TOOLS = {
    "book_reservation",
    "cancel_reservation",
    "update_reservation_baggages",
    "update_reservation_flights",
    "update_reservation_passengers",
    "send_certificate",
}

AUTH_TOOL = "get_user_details"

PENDING_RESERVATION_ID = "__pending_reservation__"

_PM_CLASSES = {
    "credit_card": CreditCard,
    "gift_card": GiftCard,
    "certificate": Certificate,
}

_RES_CLASSES = {
    "basic_economy": BasicEconomyReservation,
    "economy": ModifiableReservation,
    "business": ModifiableReservation,
}


def _payment_method_entity(pm: dict, owner_id: str):
    cls = _PM_CLASSES[pm["source"]]
    kwargs = {"id": pm["id"], "owned_by": owner_id}
    if cls is not CreditCard:
        kwargs["balance"] = float(pm.get("amount", 0.0))
    return cls(**kwargs)


def _reservation_entities(res: dict) -> list:
    """The Reservation subclass + its Segments (day statuses resolved by
    the caller, which owns the flights table)."""
    cls = _RES_CLASSES[res["cabin"]]
    return [
        cls(
            id=res["reservation_id"],
            owned_by=res["user_id"],
            origin=res["origin"],
            destination=res["destination"],
            trip_type=res["flight_type"],
            cabin=res["cabin"],
            created_at=res["created_at"],
            insurance=res["insurance"],
            total_baggages=res["total_baggages"],
            nonfree_baggages=res["nonfree_baggages"],
            num_passengers=len(res["passengers"]),
            status="cancelled" if res.get("status") == "cancelled" else "active",
        )
    ]


def _segment_entities(res: dict, flights_table: dict) -> list:
    out = []
    for i, leg in enumerate(res["flights"]):
        fn, date = leg["flight_number"], leg["date"]
        day = flights_table.get(fn, {}).get("dates", {}).get(date, {})
        out.append(
            Segment(
                id=f"{res['reservation_id']}:{i}",
                in_reservation=res["reservation_id"],
                flight_number=fn,
                date=date,
                day_status=day.get("status", "available"),
            )
        )
    return out


def seed_from_db(db_dump: dict) -> list:
    """Build the lore seed from a FlightDB dump (``db.model_dump()``).

    PassengerRecords of pre-existing reservations are not seeded — the
    <=5-passenger axiom is checked on new bookings, where the records are
    proposed; existing counts live in ``Reservation.num_passengers``.
    """
    seeds = [
        Conversation(id=CONVERSATION_ID),
        Clock(id=CLOCK_ID, now=DOMAIN_NOW),
    ]
    for uid, user in db_dump["users"].items():
        seeds.append(
            Customer(id=uid, membership=user["membership"],
                     email=user["email"], dob=user["dob"])
        )
        for pm in user["payment_methods"].values():
            seeds.append(_payment_method_entity(pm, uid))
    flights_table = db_dump["flights"]
    for res in db_dump["reservations"].values():
        seeds.extend(_reservation_entities(res))
        seeds.extend(_segment_entities(res, flights_table))
    return seeds


def _leg_key(leg) -> tuple:
    if not isinstance(leg, dict):
        leg = {"flight_number": leg.flight_number, "date": leg.date}
    return (leg["flight_number"], leg["date"])


def _itinerary(legs: list, flights_table: dict) -> tuple:
    """(origin, terminus, trip_type) implied by an ordered leg list — only
    the UNAMBIGUOUS invariants. The round-trip turnaround city cannot be
    reliably derived from leg lists (a legal direct->one-stop change shifts
    any positional guess and false-flags), so it is deliberately not
    compared: a round-trip destination change with unchanged origin is an
    under-block; tau3's reward still
    catches it via the flight mismatch.
    """
    def _o(fn):
        return flights_table[fn]["origin"]

    def _d(fn):
        return flights_table[fn]["destination"]

    numbers = [(_leg_key(leg))[0] for leg in legs]
    origin = _o(numbers[0])
    terminus = _d(numbers[-1])
    trip_type = ("round_trip"
                 if len(numbers) > 1 and terminus == origin else "one_way")
    return origin, terminus, trip_type


def sync_new_certificates(session, db_dump: dict, user_id: str) -> None:
    """Commit env-minted certificates (send_certificate) into the session so
    later payments with them resolve instead of firing false existence
    violations."""
    user = db_dump["users"].get(user_id)
    if not user:
        return
    for pm in user["payment_methods"].values():
        if pm["source"] == "certificate" and session.graph.get(pm["id"]) is None:
            verdict = session.try_commit(_payment_method_entity(pm, user_id))
            if not verdict.ok:
                raise RuntimeError(
                    f"Cannot sync certificate {pm['id']}: {verdict.repair_prompt()}"
                )


class ActionMapper:
    """Maps write-tool calls to lore entities against a live DB dump view.

    ``db_view`` must expose the *current* env state: ``reservations``,
    ``users``, ``flights`` as plain dicts (call ``db.model_dump()`` or pass
    the raw dict). The mapper re-reads it on every call, so mutations made
    by executed tools are naturally picked up.
    """

    def __init__(self, get_db_dump):
        self._get_db_dump = get_db_dump
        self._n = 0

    def _next_id(self) -> str:
        self._n += 1
        return f"act_{self._n}"

    def auth_entity(self, user_id: str) -> Authentication:
        return Authentication(id=f"auth_{user_id}", customer=user_id,
                              in_session=CONVERSATION_ID)

    def map_tool_call(self, name: str, args: dict,
                      new_reservation_id: "str | None" = None) -> list:
        """Entities to propose for a write-tool call. For book_reservation,
        pass ``new_reservation_id`` after the env assigned one (commit
        phase); omitted, the pending placeholder id is used (check phase).
        """
        db = self._get_db_dump()
        if name == "cancel_reservation":
            return [Cancellation(id=self._next_id(),
                                 cancels=args["reservation_id"])]

        if name == "update_reservation_baggages":
            return [BaggageUpdate(id=self._next_id(),
                                  updates=args["reservation_id"],
                                  payment=args["payment_id"],
                                  total_baggages=args["total_baggages"],
                                  nonfree_baggages=args["nonfree_baggages"])]

        if name == "update_reservation_passengers":
            return [PassengerUpdate(id=self._next_id(),
                                    updates=args["reservation_id"],
                                    count=len(args["passengers"]))]

        if name == "send_certificate":
            return [CertificateGrant(id=self._next_id(),
                                     to_customer=args["user_id"],
                                     amount=float(args["amount"]))]

        if name == "update_reservation_flights":
            rid = args["reservation_id"]
            new_legs = [_leg_key(leg) for leg in args["flights"]]
            current = db["reservations"].get(rid)
            current_legs = ([_leg_key(leg) for leg in current["flights"]]
                            if current else [])
            if current and sorted(new_legs) == sorted(current_legs):
                return [CabinUpdate(id=self._next_id(), updates=rid,
                                    payment=args["payment_id"],
                                    new_cabin=args["cabin"])]
            origin, destination, trip_type = _itinerary(
                args["flights"], db["flights"])
            # prior itinerary is derived from the CURRENT legs by the same
            # function, so derivation quirks cancel in the immutability rule
            if current_legs:
                p_o, p_d, p_t = _itinerary(current["flights"], db["flights"])
            else:
                p_o, p_d, p_t = origin, destination, trip_type
            common = dict(id=self._next_id(), updates=rid,
                          payment=args["payment_id"], cabin=args["cabin"],
                          new_origin=origin, new_destination=destination,
                          new_trip_type=trip_type, prior_origin=p_o,
                          prior_destination=p_d, prior_trip_type=p_t)
            # FlightUpdate (whose range check enforces basic-economy
            # no-modify against the SEEDED subtype) only applies while the
            # reservation is currently basic economy and stays basic. Any
            # flights-change where the effective cabin is (or becomes)
            # non-basic is legal per tau3 gold — the seeded subtype is
            # frozen, so route around its range check.
            currently_basic = current and current["cabin"] == "basic_economy"
            if currently_basic and args["cabin"] == "basic_economy":
                return [FlightUpdate(**common)]
            return [UpgradeFlightUpdate(**common)]

        if name == "book_reservation":
            rid = new_reservation_id or PENDING_RESERVATION_ID
            cls = _RES_CLASSES[args["cabin"]]
            origin, destination, trip_type = _itinerary(
                args["flights"], db["flights"])
            entities = [Booking(id=self._next_id(),
                                to_customer=args["user_id"])]
            entities.append(cls(
                id=rid, owned_by=args["user_id"], origin=origin,
                destination=destination, trip_type=trip_type,
                cabin=args["cabin"], created_at=DOMAIN_NOW,
                insurance=args["insurance"],
                total_baggages=args["total_baggages"],
                nonfree_baggages=args["nonfree_baggages"],
                num_passengers=len(args["passengers"]), status="active",
            ))
            for i, pax in enumerate(args["passengers"]):
                if not isinstance(pax, dict):
                    pax = pax.model_dump()
                entities.append(PassengerRecord(
                    id=f"{rid}:pax{i}", in_reservation=rid,
                    first_name=pax["first_name"],
                    last_name=pax["last_name"], dob=pax["dob"]))
            user_pms = db["users"].get(args["user_id"], {}).get(
                "payment_methods", {})
            use_cls = {"credit_card": CreditCardUse, "gift_card": GiftCardUse,
                       "certificate": CertificateUse}
            for i, pm in enumerate(args["payment_methods"]):
                if not isinstance(pm, dict):
                    pm = pm.model_dump()
                pm_id = pm["payment_id"]
                source = user_pms.get(pm_id, {}).get("source")
                if source is None:
                    # unknown method: existence check on the base use class
                    # fires via a typed guess from the id prefix
                    source = ("gift_card" if pm_id.startswith("gift_card")
                              else "certificate"
                              if pm_id.startswith("certificate")
                              else "credit_card")
                entities.append(use_cls[source](
                    id=f"{rid}:pay{i}", pays_for=rid, method=pm_id))
            return entities

        raise ValueError(f"not a guarded write tool: {name}")
