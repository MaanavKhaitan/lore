"""Trap smoke tests for the tau3 airline ontology.

Each test seeds a small world and checks that a policy violation the tau3
environment permits is caught (and that the matching legal action passes).
Run: .venv/bin/python -m pytest benchmarks/tau3_airline/test_world.py -q
"""

import sys
from .world import (
    Authentication,
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
    Customer,
    FlightUpdate,
    GiftCard,
    GiftCardUse,
    ModifiableReservation,
    PassengerRecord,
    PassengerUpdate,
    Segment,
    guard,
)

NOW = "2024-05-15T15:00:00"


def seeds(cabin="economy", insurance="no", membership="regular",
          created_at="2024-05-01T10:00:00", day_status="available"):
    cls = (BasicEconomyReservation if cabin == "basic_economy"
           else ModifiableReservation)
    return [
        Conversation(id=CONVERSATION_ID),
        Clock(id=CLOCK_ID, now=NOW),
        Customer(id="cust", membership=membership, email="a@b.c",
                 dob="1990-01-01"),
        CreditCard(id="cc", owned_by="cust"),
        GiftCard(id="gc", owned_by="cust", balance=100.0),
        Certificate(id="cert", owned_by="cust", balance=250.0),
        cls(id="res", owned_by="cust", origin="JFK", destination="SFO",
            trip_type="one_way", cabin=cabin, created_at=created_at,
            insurance=insurance, total_baggages=2, nonfree_baggages=0,
            num_passengers=2, status="active"),
        Segment(id="seg1", in_reservation="res", flight_number="HAT001",
                date="2024-05-20", day_status=day_status),
    ]


def session(**kw):
    s = guard.session(seed=seeds(**kw))
    s.try_commit(Authentication(id="auth", customer="cust",
                                in_session=CONVERSATION_ID))
    return s


def flight_update(payment="cc", origin="JFK", cabin="economy", res="res"):
    return FlightUpdate(id="fu", updates=res, payment=payment, cabin=cabin,
                        new_origin=origin, new_destination="SFO",
                        new_trip_type="one_way", prior_origin="JFK",
                        prior_destination="SFO", prior_trip_type="one_way")


# --- axiom-layer traps -----------------------------------------------------


def test_basic_economy_flight_change_is_range_violation():
    s = session(cabin="basic_economy")
    v = s.check(flight_update())
    assert not v.ok
    assert "ModifiableReservation" in v.repair_prompt()


def test_wrong_relation_targets_return_verdicts_instead_of_crashing_rules():
    s = session()
    proposals = [
        Cancellation(id="bad_cancel", cancels="cust"),
        flight_update(res="cc"),
        flight_update(payment="seg1"),
        BaggageUpdate(id="bad_bags", updates="cc", payment="cc",
                      total_baggages=3, nonfree_baggages=1),
        PassengerUpdate(id="bad_passengers", updates="cc", count=2),
        GiftCardUse(id="bad_use", pays_for="cc", method="cust"),
        CertificateGrant(id="bad_grant", to_customer="res", amount=100),
    ]
    for proposal in proposals:
        verdict = s.check(proposal)
        assert not verdict.ok
        assert any(v.check == "range" for v in verdict.rejects)


def test_basic_economy_cabin_only_change_is_legal():
    s = session(cabin="basic_economy")
    v = s.check(CabinUpdate(id="cu", updates="res", payment="cc",
                            new_cabin="economy"))
    assert v.ok, v.repair_prompt()


def test_certificate_cannot_pay_for_update():
    s = session()
    v = s.check(flight_update(payment="cert"))
    assert not v.ok
    assert "NonCertificateMethod" in v.repair_prompt()


def test_at_most_three_gift_cards_per_reservation():
    s = session()
    for i in range(3):
        s.try_commit(GiftCard(id=f"gc{i}", owned_by="cust", balance=10.0))
        assert s.try_commit(GiftCardUse(id=f"use{i}", pays_for="res",
                                        method=f"gc{i}")).ok
    s.try_commit(GiftCard(id="gc3", owned_by="cust", balance=10.0))
    v = s.check(GiftCardUse(id="use3", pays_for="res", method="gc3"))
    assert not v.ok  # max_per_target=3


def test_at_most_one_certificate_per_reservation():
    s = session()
    assert s.try_commit(CertificateUse(id="cu1", pays_for="res",
                                       method="cert")).ok
    s.try_commit(Certificate(id="cert2", owned_by="cust", balance=50.0))
    v = s.check(CertificateUse(id="cu2", pays_for="res", method="cert2"))
    assert not v.ok


def test_at_most_five_passengers_per_reservation():
    s = session()
    s.try_commit(ModifiableReservation(
        id="res2", owned_by="cust", origin="JFK", destination="SFO",
        trip_type="one_way", cabin="economy", created_at=NOW,
        insurance="no", total_baggages=0, nonfree_baggages=0,
        num_passengers=5, status="active"))
    for i in range(5):
        assert s.try_commit(PassengerRecord(
            id=f"pax{i}", in_reservation="res2", first_name="P",
            last_name=str(i), dob="1990-01-01")).ok
    v = s.check(PassengerRecord(id="pax5", in_reservation="res2",
                                first_name="P", last_name="5",
                                dob="1990-01-01"))
    assert not v.ok  # max_per_target=5


def test_unknown_payment_method_is_existence_violation():
    s = session()
    v = s.check(flight_update(payment="cc_not_in_profile"))
    assert not v.ok  # "must already be in user profile" == existence check


def test_one_customer_per_conversation():
    s = session()
    s.try_commit(Customer(id="other", membership="gold", email="x@y.z",
                          dob="1980-01-01"))
    v = s.check(Authentication(id="auth2", customer="other",
                               in_session=CONVERSATION_ID))
    assert not v.ok  # max_per_target=1 on the conversation singleton


# --- rule-layer traps --------------------------------------------------------


def test_unauthenticated_action_rejected():
    s = guard.session(seed=seeds())  # no Authentication committed
    v = s.check(Cancellation(id="c1", cancels="res"))
    assert not v.ok
    assert "authenticated" in v.repair_prompt()


def test_ineligible_cancellation_rejected():
    # regular member, economy, no insurance, booked >24h ago, flights fine
    s = session()
    v = s.check(Cancellation(id="c1", cancels="res"))
    assert not v.ok
    assert "not eligible" in v.repair_prompt()


def test_cancellation_within_24h_allowed():
    s = session(created_at="2024-05-15T10:00:00")
    assert s.check(Cancellation(id="c1", cancels="res")).ok


def test_cancellation_business_allowed():
    s = session(cabin="business")
    assert s.check(Cancellation(id="c1", cancels="res")).ok


def test_cancellation_airline_cancelled_allowed():
    s = session(day_status="cancelled")
    assert s.check(Cancellation(id="c1", cancels="res")).ok


def test_flown_segment_blocks_cancellation_even_if_insured():
    s = session(insurance="yes", day_status="landed")
    v = s.check(Cancellation(id="c1", cancels="res"))
    assert not v.ok
    assert "transfer" in v.repair_prompt()


def test_origin_change_rejected():
    s = session()
    v = s.check(flight_update(origin="BOS"))
    assert not v.ok
    assert "origin" in v.repair_prompt()


def test_cabin_change_with_flown_segment_rejected():
    s = session(day_status="flying")
    v = s.check(CabinUpdate(id="cu", updates="res", payment="cc",
                            new_cabin="business"))
    assert not v.ok


def test_baggage_removal_rejected():
    s = session()  # seeded with 2 bags
    v = s.check(BaggageUpdate(id="b1", updates="res", payment="cc",
                              total_baggages=1, nonfree_baggages=0))
    assert not v.ok
    assert "removed" in v.repair_prompt() or "reduce" in v.repair_prompt()


def test_baggage_undercharge_rejected():
    # regular member, economy: 1 free bag x 2 passengers = 2 free; 4 total
    # means nonfree must be >= 2
    s = session()
    v = s.check(BaggageUpdate(id="b1", updates="res", payment="cc",
                              total_baggages=4, nonfree_baggages=1))
    assert not v.ok
    assert s.check(BaggageUpdate(id="b2", updates="res", payment="cc",
                                 total_baggages=4, nonfree_baggages=2)).ok


def test_passenger_count_change_rejected():
    s = session()
    v = s.check(PassengerUpdate(id="p1", updates="res", count=3))
    assert not v.ok
    assert s.check(PassengerUpdate(id="p2", updates="res", count=2)).ok


def test_compensation_to_regular_uninsured_economy_rejected():
    s = session()
    v = s.check(CertificateGrant(id="g1", to_customer="cust", amount=200.0))
    assert not v.ok


def test_compensation_wrong_amount_flags_but_commits():
    s = session(membership="gold", day_status="cancelled")
    v = s.try_commit(CertificateGrant(id="g1", to_customer="cust",
                                      amount=137.0))
    assert v.ok  # severity="flag": commits...
    assert v.violations  # ...but surfaces the schedule mismatch


def test_compensation_correct_amount_clean():
    s = session(membership="gold", day_status="cancelled")
    v = s.try_commit(CertificateGrant(id="g1", to_customer="cust",
                                      amount=200.0))  # $100 x 2 passengers
    assert v.ok and not v.violations




def _upgrade(s, cabin="economy"):
    from .world import UpgradeFlightUpdate
    return s.try_commit(UpgradeFlightUpdate(
        id="up1", updates="res", payment="cc", cabin=cabin,
        new_origin="JFK", new_destination="SFO", new_trip_type="one_way",
        prior_origin="JFK", prior_destination="SFO",
        prior_trip_type="one_way"))


def test_upgraded_basic_econ_gets_new_baggage_allowance():
    # gold member, basic econ (2 free/pax) upgraded to economy (3 free/pax):
    # 3 bags for 2 pax => 6 free after upgrade, 4 free before
    s = session(cabin="basic_economy", membership="gold")
    assert _upgrade(s).ok
    v = s.check(BaggageUpdate(id="b1", updates="res", payment="cc",
                              total_baggages=5, nonfree_baggages=0))
    assert v.ok, v.repair_prompt()  # 5 <= 6 free post-upgrade


def test_upgrade_to_business_enables_cancellation():
    # tau3 gold's own path in task 7: upgrade to business, then cancel
    s = session(cabin="basic_economy")
    assert _upgrade(s, cabin="business").ok
    assert s.check(Cancellation(id="c1", cancels="res")).ok


def test_pure_basic_econ_flight_change_still_range_violation():
    s = session(cabin="basic_economy")
    v = s.check(FlightUpdate(id="fu", updates="res", payment="cc",
                             cabin="basic_economy", new_origin="JFK",
                             new_destination="SFO", new_trip_type="one_way",
                             prior_origin="JFK", prior_destination="SFO",
                             prior_trip_type="one_way"))
    assert not v.ok and "ModifiableReservation" in v.repair_prompt()


def test_booking_requires_auth():  # review #2
    from .world import Booking
    s = guard.session(seed=seeds())  # no Authentication
    v = s.check(Booking(id="b1", to_customer="cust"))
    assert not v.ok and "authenticated" in v.repair_prompt()
    s.try_commit(Authentication(id="a", customer="cust",
                                in_session=CONVERSATION_ID))
    assert s.check(Booking(id="b2", to_customer="cust")).ok


def test_no_update_after_in_session_cancellation():  # review #5
    s = session(cabin="business")
    assert s.try_commit(Cancellation(id="c1", cancels="res")).ok
    v = s.check(BaggageUpdate(id="b1", updates="res", payment="cc",
                              total_baggages=3, nonfree_baggages=1))
    assert not v.ok and "cancelled" in v.repair_prompt()


def test_compensation_requires_disrupted_flight():  # review #7
    s = session(membership="gold")  # all segments available
    v = s.check(CertificateGrant(id="g1", to_customer="cust", amount=200.0))
    assert not v.ok


def test_itinerary_quirks_cancel():  # review #1: derived-vs-derived compare
    s = session()
    fu = FlightUpdate(id="fu", updates="res", payment="cc", cabin="economy",
                      new_origin="ATL", new_destination="LAS",
                      new_trip_type="round_trip", prior_origin="ATL",
                      prior_destination="LAS", prior_trip_type="round_trip")
    assert s.check(fu).ok  # both sides derived identically -> no false block


def test_delayed_compensation_requires_rebooking():  # task-5 gap fix
    s = session(membership="gold", day_status="delayed")
    v = s.check(CertificateGrant(id="g1", to_customer="cust", amount=200.0))
    assert not v.ok and "re-booking or cancelling" in v.repair_prompt()
    # after an in-session cancellation (delayed flight is insured? use
    # business cabin so the cancel itself is eligible), compensation passes
    s2 = session(membership="gold", day_status="delayed", cabin="business")
    assert s2.try_commit(Cancellation(id="c1", cancels="res")).ok
    assert s2.check(CertificateGrant(id="g2", to_customer="cust",
                                     amount=200.0)).ok


if __name__ == "__main__":
    import traceback

    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
