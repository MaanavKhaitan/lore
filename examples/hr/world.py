"""The HR approval-chain lore: employees, expense reports — and the axioms
an agent must not violate.

The flagship payoff of the inference layer: declaring ``reports_to`` as
``transitive=True, irreflexive=True`` gives cycle detection for free. A
proposed edge that closes a reporting loop derives a self-edge, the
irreflexive check catches it, and the repair prompt renders the exact chain.
"""

from lore import Entity, Graph, Lore, Relation, relation

lore = Lore("hr")


@lore.entity
class Employee(Entity):
    name: str
    # transitive: if A reports to B and B to C, A reports to C.
    # irreflexive: nobody is in their own chain — org charts can't loop.
    reports_to: Relation["Employee"] | None = relation(
        transitive=True, irreflexive=True, default=None
    )


@lore.entity
class ExpenseReport(Entity):
    amount: float
    filed_by: Relation[Employee]
    approved_by: Relation[Employee]


@lore.rule(
    message=(
        "Expense {obj.id} was approved by {obj.approved_by}, who is not in "
        "{obj.filed_by}'s management chain."
    )
)
def approver_in_chain(report: ExpenseReport, graph: Graph) -> bool:
    """The SOX control: the approver must be somewhere above the filer.

    ``graph.reachable`` follows the transitive closure, so the CEO approving a
    deep report passes without anyone enumerating chains — and self-approval
    fails naturally, because irreflexivity keeps you out of your own chain.
    """
    return report.approved_by in graph.reachable(report.filed_by, "Employee.reports_to")


guard = lore.compile()
