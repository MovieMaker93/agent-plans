# SPDX-License-Identifier: Apache-2.0
"""Small task builder for scheduler tests."""

from decimal import Decimal

from planner.model import Actuals, Artifact, Budget, Dependency, Estimate, Task


def dep(task: str, type: str = "FS", lag=0) -> Dependency:
    return Dependency(task=task, type=type, lag_hours=Decimal(str(lag)))


def task(
    tid: str,
    optimistic,
    likely=None,
    pessimistic=None,
    assignee: str = "worker",
    deps: list[Dependency] | None = None,
    status: str = "planned",
) -> Task:
    if likely is None:
        likely = optimistic
    if pessimistic is None:
        pessimistic = likely
    return Task(
        id=tid,
        title=tid,
        task_type="test",
        objective="Do the task.",
        inputs=[],
        outputs=[Artifact(artifact=f"{tid}.out", type="text")],
        depends_on=list(deps or []),
        estimate_hours=Estimate(
            Decimal(str(optimistic)),
            Decimal(str(likely)),
            Decimal(str(pessimistic)),
        ),
        required_capabilities=["test"],
        assignee=assignee,
        acceptance_criteria=["It meets the contract."],
        risk="low",
        reversible=True,
        needs_human=False,
        budget=Budget(0, Decimal(0), Decimal(0)),
        max_retries=1,
        status=status,
        actuals=Actuals(None, None, None, 0),
    )
