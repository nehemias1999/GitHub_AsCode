"""The plan model shared by every automation.

A plan is a flat list of operations. Each one answers three questions about a single
resource: what would be done to it (action), whether that is safe to do now (status),
and why (reason). Nothing else. Keeping the model this small is what lets one reviewer
read a plan for three different resource families without learning three vocabularies.

The vocabulary is deliberately the same one a repository that DOES write would use, so
that somebody reading a plan from either does not have to learn two languages. What
differs is who executes: here, a person. Nothing in this module writes, and there is no
apply for it to gate - see docs/adr/0001-write-boundary.md.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

# Status describes whether the operation may proceed.
#   ok        - the live state already matches; nothing to do.
#   pending   - a change is required and is safe to make.
#   warning   - the change will proceed but a human should read the reason.
#   protected - deliberately not changed, to avoid destroying something.
#   blocked   - nothing further about this resource could be determined.
PLAN_STATUS = ("ok", "pending", "warning", "protected", "blocked")

# Action describes what would happen to the resource.
PLAN_ACTION = (
    "create",  # the resource does not exist and will be created
    "exists",  # present and already correct
    "adopt",  # present, created outside this repository, brought under management as is
    "update",  # a property will be changed
    "set",  # a value will be written into an existing container
    "add",  # a member or child will be added
    "reconcile",  # a collection will be rewritten to match the declaration
    "rename",  # the resource will be renamed
    "authorize",  # a permission or access grant will be given
    "validate",  # a check with no possible write
    "resolve",  # a human must resolve an ambiguity before anything can proceed
    "manual",  # deliberately not automated; a human performs it
    "skip",  # intentionally out of scope for this run
)


class PlanVocabularyError(ValueError):
    """An action or status outside the closed vocabulary."""


@dataclass(frozen=True)
class Operation:
    """One operation: what, to which resource, whether it may proceed, and why."""

    resource: str
    name: str
    action: str
    status: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "resource": self.resource,
            "name": self.name,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class Plan:
    """A command, what it was about, when it ran, and the operations it produced."""

    command: str
    target: str
    generated_at: str
    operations: list[Operation] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "command": self.command,
            "target": self.target,
            "generatedAt": self.generated_at,
            "operations": [operation.as_dict() for operation in self.operations],
        }


def new_plan(command: str, target: str, generated_at: str | None = None) -> Plan:
    """Create an empty plan for a target.

    Args:
        command: Command that produced the plan, for example 'inventory' or 'plan'.
        target: What the plan is about.
        generated_at: Timestamp recorded in the plan. Defaults to now in UTC.
            Injectable so a test can assert on a fixed value.

    Returns:
        An empty Plan.

    Example:
        >>> new_plan("plan", "EXAMPLE-owner", generated_at="2026-01-01T00:00:00Z").target
        'EXAMPLE-owner'
    """
    if generated_at is None:
        generated_at = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    return Plan(command=command, target=target, generated_at=generated_at)


def new_operation(resource: str, name: str, action: str, status: str, reason: str) -> Operation:
    """Create one plan operation, checked against the closed vocabularies.

    A free-text status is how a plan turns into prose that nothing can enforce - in
    particular, a caller cannot act on a status it does not recognise, and the exit
    code of a run is exactly such a caller.

    Args:
        resource: Resource family, for example 'Repository'.
        name: Name of the specific resource.
        action: One of PLAN_ACTION.
        status: One of PLAN_STATUS.
        reason: Why. Written for the person approving the plan, not for a log parser.

    Returns:
        An Operation.

    Raises:
        PlanVocabularyError: The action or status is not in the vocabulary.

    Example:
        >>> new_operation("Repository", "EXAMPLE-repo", "exists", "ok", "Matches.").status
        'ok'
    """
    if action not in PLAN_ACTION:
        raise PlanVocabularyError(
            f"Unknown plan action '{action}'. Valid actions: {', '.join(PLAN_ACTION)}."
        )
    if status not in PLAN_STATUS:
        raise PlanVocabularyError(
            f"Unknown plan status '{status}'. Valid statuses: {', '.join(PLAN_STATUS)}."
        )
    return Operation(resource=resource, name=name, action=action, status=status, reason=reason)


def add_operation(plan: Plan, resource: str, name: str, status: Any) -> None:
    """Append an operation described by a status object.

    Accepts what a `*_status` function returns, so a caller that already has an
    action/status/reason triple does not have to unpack it.

    Args:
        plan: Plan to append to.
        resource: Resource family.
        name: Resource name.
        status: An object or mapping carrying action, status and reason.

    Example:
        >>> p = new_plan("plan", "t", generated_at="x")
        >>> verdict = {"action": "exists", "status": "ok", "reason": "Matches."}
        >>> add_operation(p, "Repository", "EXAMPLE-repo", verdict)
        >>> len(p.operations)
        1
    """
    if isinstance(status, dict):
        action, state, reason = status["action"], status["status"], status["reason"]
    else:
        action, state, reason = status.action, status.status, status.reason
    plan.operations.append(new_operation(resource, name, action, state, reason))


def plan_summary(plan: Plan) -> dict:
    """Count the operations of a plan by status.

    Args:
        plan: The plan.

    Returns:
        A dict with 'total' and one count per status, in vocabulary order.

    Example:
        >>> plan_summary(new_plan("plan", "t", generated_at="x"))["total"]
        0
    """
    summary = {"total": len(plan.operations)}
    for status in PLAN_STATUS:
        summary[status] = sum(1 for operation in plan.operations if operation.status == status)
    return summary


def is_blocked(plan: Plan) -> bool:
    """Whether anything in the plan could not be determined.

    One function rather than an inline check, so the question is one testable
    statement instead of a convention each module re-implements. Its answer is what
    the entry points turn into an exit code: a run whose plan holds a blocked
    operation exits 2, not 0. In the inherited code it was called and its answer
    thrown away in a log line, so a scheduler saw success.

    Args:
        plan: The plan.

    Returns:
        True when at least one operation is blocked.

    Example:
        >>> is_blocked(new_plan("plan", "t", generated_at="x"))
        False
    """
    return any(operation.status == "blocked" for operation in plan.operations)


def format_plan_summary(plan: Plan, maximum_item: int = 15) -> list[str]:
    """Render a readable plan summary as lines.

    Returns lines rather than printing them, which is the one real change from the
    PowerShell version: `Write-PlanSummary` wrote to the information stream, and a
    pure function is testable without capturing output.

    Operations that are already `ok` are counted but not listed: on an idempotent
    re-run they are almost the entire plan, and printing hundreds of "already correct"
    lines is what trains people to stop reading the output.

    Args:
        plan: The plan.
        maximum_item: Maximum operations to list per status group.

    Returns:
        The lines to print.

    Example:
        >>> format_plan_summary(new_plan("inventory", "t", generated_at="x"))[-1].strip()
        'Nothing outstanding: inventory reports live state and compares nothing.'
    """
    summary = plan_summary(plan)
    lines = [
        f"Plan for '{plan.target}' ({plan.command}): {summary['total']} operation(s) - "
        f"ok {summary['ok']}, pending {summary['pending']}, warning {summary['warning']}, "
        f"protected {summary['protected']}, blocked {summary['blocked']}."
    ]

    for status in ("blocked", "warning", "pending", "protected"):
        items = [operation for operation in plan.operations if operation.status == status]
        if not items:
            continue
        lines.append(f"  [{status}]")
        for item in items[:maximum_item]:
            lines.append(f"    {item.action:<10} {item.resource}: {item.name} - {item.reason}")
        if len(items) > maximum_item:
            # Never hide a truncation. A silent cap reads as full coverage.
            lines.append(
                f"    ... {len(items) - maximum_item} more {status} operation(s) not listed; "
                "see the report file."
            )

    if summary["pending"] == 0 and summary["blocked"] == 0:
        # Worded by command, because inventory does not read the declaration at all -
        # it reports what is live. The single sentence this used to print claimed the
        # live state "already matches the declaration", which after an inventory
        # announces the result of a comparison that never ran.
        #
        # It branches on the ladder vocabulary, not on any resource, so the shared
        # layer stays free of domain rules.
        if plan.command == "inventory":
            lines.append(
                "  Nothing outstanding: inventory reports live state and compares nothing."
            )
        else:
            lines.append("  Nothing to change: the live state already matches the declaration.")

    return lines
