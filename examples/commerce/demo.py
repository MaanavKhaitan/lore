"""The double-refund demo — no API key, no LLM, fully deterministic.

Seeds a session with a small world, then scripts four proposals an agent might
make and prints each verdict plus the repair prompt that would be fed back.

Run:  python examples/commerce/demo.py
      python examples/commerce/demo.py --html commerce.html   # + the viz export
"""

import argparse

from lore.viz import TraceRecorder, to_html

from world import Customer, Order, Refund, SupportRep, guard

parser = argparse.ArgumentParser(description="The double-refund demo.")
parser.add_argument("--html", metavar="PATH", help="export the run as a self-contained HTML viewer")
args = parser.parse_args()

recorder = TraceRecorder()
session = guard.session(
    recorder=recorder,
    seed=[
        Customer(id="cust_1", name="Ada"),
        SupportRep(id="rep_1", name="Sam"),
        Order(id="ord_1", status="refunded", total=100.0, placed_by="cust_1"),
        Order(id="ord_2", status="paid", total=250.0, placed_by="cust_1"),
        # ord_3 exists so each scripted proposal below shows exactly one kind
        # of violation (every other order ends up refunded).
        Order(id="ord_3", status="shipped", total=80.0, placed_by="cust_1"),
        Refund(id="ref_0", amount=100.0, refunds="ord_1", paid_to="cust_1"),
    ]
)

PROPOSALS = [
    (
        "A valid refund",
        Refund(id="ref_1", amount=40.0, refunds="ord_2", paid_to="cust_1"),
    ),
    (
        "A second refund on the already-refunded order (cross-turn!)",
        Refund(id="ref_2", amount=40.0, refunds="ord_2", paid_to="cust_1"),
    ),
    (
        "A refund paid to the support rep",
        Refund(id="ref_3", amount=20.0, refunds="ord_3", paid_to="rep_1"),
    ),
    (
        "A refund of an order that does not exist",
        Refund(id="ref_4", amount=20.0, refunds="ord_999", paid_to="cust_1"),
    ),
]

for i, (title, refund) in enumerate(PROPOSALS, start=1):
    print(f"--- Proposal {i}: {title}")
    print(f"    {refund!r}")
    verdict = session.propose(refund)
    if verdict.ok:
        session.commit()
        print("    verdict: OK — committed\n")
        continue
    print("    verdict: REJECTED")
    print("    repair prompt fed back to the agent:")
    for line in verdict.repair_prompt().splitlines():
        print(f"      {line}")
    print()

session.rollback()  # leave no dangling proposal (also closes the trace cleanly)
if args.html:
    print(f"wrote {to_html(guard, trace=recorder, out=args.html)}")
