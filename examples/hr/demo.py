"""The approval-chain demo — no API key, no LLM, fully deterministic.

Seeds the CEO, builds the org chart through committed proposals (so the cycle
rejection can name the exact step each link landed), then scripts four
proposals an agent might make and prints each verdict plus the repair prompt
that would be fed back.

Run:  python examples/hr/demo.py
"""

from world import Employee, ExpenseReport, guard

session = guard.session(seed=[Employee(id="ceo", name="Grace")])

# The org chart accumulates across turns, like any agent session would.
session.try_commit(Employee(id="mgr_2", name="Miri", reports_to="ceo"))  # step 1
session.try_commit(Employee(id="emp_9", name="Eve", reports_to="mgr_2"))  # step 2
session.try_commit(Employee(id="mgr_3", name="Omar", reports_to="ceo"))  # step 3

PROPOSALS = [
    (
        "A new hire reporting to themselves",
        Employee(id="emp_x", name="Sam", reports_to="emp_x"),
    ),
    (
        "The CEO reporting to a subordinate (closes a cycle two links away)",
        Employee(id="ceo", name="Grace", reports_to="emp_9"),
    ),
    (
        "An expense approved outside the filer's management chain",
        ExpenseReport(id="exp_1", amount=120.0, filed_by="emp_9", approved_by="mgr_3"),
    ),
    (
        "A valid approval by the CEO — two levels up, via the transitive chain",
        ExpenseReport(id="exp_2", amount=120.0, filed_by="emp_9", approved_by="ceo"),
    ),
]

for i, (title, proposal) in enumerate(PROPOSALS, start=1):
    print(f"--- Proposal {i}: {title}")
    print(f"    {proposal!r}")
    verdict = session.propose(proposal)
    if verdict.ok:
        session.commit()
        print("    verdict: OK — committed\n")
        continue
    print("    verdict: REJECTED")
    print("    repair prompt fed back to the agent:")
    for line in verdict.repair_prompt().splitlines():
        print(f"      {line}")
    print()
