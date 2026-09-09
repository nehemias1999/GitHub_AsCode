"""The repository rules, as pure functions.

Every function here takes values and returns values. Nothing reaches the network, which
is not a testing convenience but the design: drift is defined against the PAYLOAD that
would be sent, not against the declaration, so if the payload is a pure value then drift
is a comparison of values and a second plan returning nothing pending is an assertion a
test can make offline.

Two rules here are the ones that stop this repository destroying something.

Topics are a replace-the-whole-collection API. `PUT /repos/{o}/{r}/topics` has no
per-topic route, so sending the declared list removes every topic somebody added and
nobody declared. `topic_union` is the answer: the payload is the union of live and
declared, and the undeclared ones are reported as preserved. Removing one is
reconcile's job, behind its own confirmation, and reconcile does not exist yet.

Absence is not absence. A repository declared but not returned by the API might not
exist, or might exist where this token cannot see it - GitHub answers 404 for both. So a
missing declared repository is never reported as "create it"; it is reported as
something a person has to resolve.
"""

import re
from dataclasses import dataclass
from typing import Any

from github_as_code import plan as plan_module

# The fields the inventory reads, and the only fields it reads. Listed once so the
# snapshot, the report and the schema cannot drift apart.
SNAPSHOT_PROPERTY = (
    "name",
    "full_name",
    "private",
    "visibility",
    "archived",
    "fork",
    "is_template",
    "description",
    "homepage",
    "default_branch",
    "language",
    "topics",
    "has_issues",
    "has_wiki",
    "has_projects",
    "has_discussions",
    "pushed_at",
    "updated_at",
    "created_at",
    "size",
    "open_issues_count",
)

_TOPIC_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$", re.ASCII)
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$", re.ASCII)


class DeclarationError(ValueError):
    """A declared value cannot be used against the API."""


@dataclass(frozen=True)
class TopicUnion:
    """The payload that would be sent, and what it preserves."""

    payload: list[str]
    added: list[str]
    preserved: list[str]
    changed: bool


@dataclass(frozen=True)
class Difference:
    """One field that differs, with both sides, so the approver reads fields."""

    field: str
    live: str
    declared: str

    def as_dict(self) -> dict:
        return {"field": self.field, "live": self.live, "declared": self.declared}


@dataclass(frozen=True)
class Status:
    """The verdict for one resource, in the vocabulary the plan enforces."""

    action: str
    status: str
    reason: str
    difference: list[Difference] = ()

    def __post_init__(self):
        # The vocabulary is closed, and this is where a domain module could quietly
        # step outside it. Checking here rather than only at add_operation means a
        # status is wrong at the point it is built, not three layers away.
        if self.action not in plan_module.PLAN_ACTION:
            raise plan_module.PlanVocabularyError(f"Unknown plan action '{self.action}'.")
        if self.status not in plan_module.PLAN_STATUS:
            raise plan_module.PlanVocabularyError(f"Unknown plan status '{self.status}'.")


def snapshot_properties() -> tuple[str, ...]:
    """The repository fields the inventory reads.

    Exported so the test suite and the configuration schema assert against the same
    list the snapshot builds from, instead of restating it.

    Returns:
        The property names, in report order.

    Example:
        >>> "topics" in snapshot_properties()
        True
    """
    return SNAPSHOT_PROPERTY


def format_topic_name(topic: str) -> str:
    """Normalise one topic to the form GitHub actually stores.

    GitHub lowercases a topic and accepts only letters, digits and hyphens, up to 50
    characters, starting with a letter or a digit.

    Normalising here rather than trusting the declaration is what makes the union
    correct. A declaration saying "PowerShell" and a live topic "powershell" are the
    same topic; comparing them raw makes every plan report a change that a subsequent
    plan reports again, because the API stored the lowercase form. That is an
    idempotency failure, and idempotency is the acceptance criterion.

    An unusable topic is rejected rather than silently mangled: quietly turning "c#"
    into "c" gives the account a topic nobody chose.

    Args:
        topic: The declared topic.

    Returns:
        The normalised topic.

    Raises:
        DeclarationError: The topic is empty, too long, or not storable.

    Example:
        >>> format_topic_name("PowerShell")
        'powershell'
    """
    normalized = topic.strip().lower()

    if not normalized:
        raise DeclarationError("A topic cannot be empty.")
    if len(normalized) > 50:
        raise DeclarationError(
            f"The topic '{normalized}' is {len(normalized)} characters. GitHub allows at most 50."
        )
    if not _TOPIC_PATTERN.match(normalized):
        raise DeclarationError(
            f"The topic '{normalized}' is not a valid GitHub topic. Allowed: lowercase letters, "
            "digits and hyphens, starting with a letter or a digit. It is not normalized "
            "automatically, because turning 'c#' into 'c' would give the account a topic nobody "
            "chose."
        )

    return normalized


def format_repository_name(name: str) -> str:
    """Validate a declared repository name, and return it unchanged.

    The schema constrains this with a pattern, and the built-in validator now enforces
    it - which the reduced PowerShell one did not. This function stays anyway, for two
    reasons that are both about what happens after validation.

    The message is better: it names `owner/repo` as the likely mistake rather than
    printing a regular expression.

    And the name becomes a path segment in phase 3, when `repos/{owner}/{repo}/topics`
    arrives. `build_uri` escapes the query, not the path. What stands between `../..`
    and a request is exactly this.

    Returns the name unchanged rather than normalising it. A repository name is
    case-sensitive on the way in and GitHub preserves it, so there is nothing safe to
    normalise - unlike a topic, which the API lowercases and which therefore has to be
    lowercased here to keep the comparison idempotent.

    Args:
        name: The declared repository name.

    Returns:
        The name, unchanged.

    Raises:
        DeclarationError: The name is empty, too long, contains a character GitHub does
            not accept, or is a relative path segment.

    Example:
        >>> format_repository_name("EXAMPLE-service")
        'EXAMPLE-service'
    """
    if not name or not name.strip():
        raise DeclarationError("A repository name cannot be empty.")
    if len(name) > 100:
        raise DeclarationError(
            f"The repository name is {len(name)} characters. GitHub allows at most 100."
        )
    # GitHub accepts letters, digits, hyphen, underscore and dot. Notably NOT the
    # slash - a value containing one is either owner/repo, which is the single most
    # common mistake in this field, or a traversal attempt.
    if not _NAME_PATTERN.match(name):
        reason = (
            "it contains '/', so it is probably owner/repo - declare the repository name "
            "alone, because the owner comes from the environment"
            if "/" in name
            else "allowed characters are letters, digits, hyphen, underscore and dot"
        )
        raise DeclarationError(
            f"The repository name '{name}' is not a valid GitHub repository name: {reason}."
        )
    # '.' and '..' are valid against the character class above and are path traversal
    # once a name becomes a URL segment.
    if name in (".", ".."):
        raise DeclarationError(
            f"The repository name '{name}' is a relative path segment, not a name."
        )

    return name


def topic_union(live_topics: list[str] | None, declared_topics: list[str] | None) -> TopicUnion:
    """Build the topic payload: everything live, plus everything declared.

    THIS IS THE FUNCTION THAT STOPS TOPICS BEING DESTROYED.

    `PUT /repos/{owner}/{repo}/topics` replaces the entire collection and there is no
    per-topic route. The obvious implementation - send the declared topics - removes
    every topic that was added by hand and never written down. That is a real risk
    rather than a hypothetical one on any account with older repositories: whatever is
    on a repository nobody has touched in months is exactly the kind of thing nobody
    remembers declaring.

    So the payload is the union, and the result also says which live topics were not
    declared, so the plan can report them as preserved rather than silently keeping
    them.

    The order is sorted, because the API returns topics in an unspecified order and an
    unsorted payload makes the same declaration produce two different payloads between
    runs - which the drift comparison would then read as a change.

    Args:
        live_topics: Topics currently on the repository.
        declared_topics: Topics from the declaration.

    Returns:
        A TopicUnion.

    Example:
        >>> topic_union(["kept"], ["Added"]).payload
        ['added', 'kept']
    """
    live = [topic.strip().lower() for topic in (live_topics or []) if topic]
    declared = [format_topic_name(topic) for topic in (declared_topics or []) if topic]

    return TopicUnion(
        payload=sorted(set(live) | set(declared)),
        added=sorted({topic for topic in declared if topic not in live}),
        preserved=sorted({topic for topic in live if topic not in declared}),
        changed=bool({topic for topic in declared if topic not in live}),
    )


def new_snapshot(repository: dict) -> dict:
    """Reduce an API repository object to the fields the inventory reports.

    The API returns around 80 properties per repository, most of them URL templates.
    Carrying all of them into a report makes even a small inventory an unreadable
    megabyte, and makes a diff between two runs meaningless.

    A property the API did not send becomes None rather than being absent, so every
    snapshot has the same shape and a report writer never has to test for a missing
    key. `topics` is the exception: it becomes an empty list, because a collection that
    is sometimes None and sometimes a list is the shape that breaks a count.

    Args:
        repository: One repository object from the API.

    Returns:
        The snapshot, in report order, with a `license` field holding the SPDX
        identifier or None.

    Example:
        >>> new_snapshot({"name": "EXAMPLE-repo"})["topics"]
        []
    """
    snapshot = {name: repository.get(name) for name in SNAPSHOT_PROPERTY}

    snapshot["topics"] = sorted(str(item) for item in (snapshot["topics"] or []) if item)

    # The licence arrives as an object, and the only part worth reporting is the
    # identifier. A repository with no licence has None here, not an empty object, so
    # "no licence" and "a licence with no name" cannot be confused. NOASSERTION is
    # GitHub's way of saying it found a licence file it could not identify, which is
    # not an identifier.
    licence = repository.get("license")
    identifier = None
    if isinstance(licence, dict):
        spdx = licence.get("spdx_id")
        if spdx and spdx != "NOASSERTION":
            identifier = str(spdx)
    snapshot["license"] = identifier

    return snapshot


def repository_status(declaration: dict, snapshot: dict | None) -> Status:
    """Compare one declared repository against its live state.

    The four cases that matter, and why each gets the status it gets:

    Declared but not live -> resolve / blocked. NOT create. GitHub answers 404 both for
    a repository that does not exist and for one this token cannot see, so the tool
    genuinely does not know which it is, and blocked is what "could not be determined"
    means.

    Archived -> skip / protected. An archived repository is read-only and every write
    against it fails. Reporting it as pending would produce a plan whose apply cannot
    succeed.

    Declared, live, and different -> update / pending. A change is required and it is
    safe. The detail of what differs is in `difference`, so the approver reads the
    fields rather than the word.

    Declared, live, and the same -> exists / ok.

    Args:
        declaration: The declared entry: name, and optionally class, description,
            homepage, topics.
        snapshot: The snapshot from `new_snapshot`, or None when absent.

    Returns:
        A Status.

    Example:
        >>> repository_status({"name": "EXAMPLE-repo"}, None).status
        'blocked'
    """
    if snapshot is None:
        return Status(
            action="resolve",
            status="blocked",
            reason=(
                "Declared, but the API did not return it. On GitHub that means either it does "
                "not exist or this token cannot see it, and the two are indistinguishable from "
                "here. Check the name, then check the token's repository access."
            ),
            difference=[],
        )

    if snapshot.get("archived"):
        return Status(
            action="skip",
            status="protected",
            reason=(
                "Archived, so it is read-only and every write against it would fail. "
                "Unarchiving is deliberately not automated; do it in the web interface if the "
                "repository is coming back into use."
            ),
            difference=[],
        )

    difference: list[Difference] = []

    for name in ("description", "homepage"):
        if name not in declaration or declaration[name] is None:
            continue
        # The API returns an unset description as null and an unset homepage as an
        # empty string, inconsistently. Both mean "nothing there", so both normalise to
        # an empty string before comparison - otherwise the plan reports a change that
        # the apply cannot make, forever.
        live_value = "" if snapshot.get(name) is None else str(snapshot.get(name))
        declared_value = str(declaration[name])
        if declared_value != live_value:
            difference.append(Difference(field=name, live=live_value, declared=declared_value))

    if declaration.get("topics") is not None:
        union = topic_union(snapshot.get("topics"), declaration["topics"])
        if union.changed:
            difference.append(
                Difference(
                    field="topics",
                    live=", ".join(snapshot.get("topics") or []),
                    declared=", ".join(union.added),
                )
            )

    if not difference:
        return Status(
            action="exists",
            status="ok",
            reason="Live state already matches the declaration.",
            difference=[],
        )

    fields = ", ".join(item.field for item in difference)
    return Status(
        action="update",
        status="pending",
        reason=(
            f"Differs from the declaration in: {fields}. repo-metadata would change these; "
            "this inventory only reports them."
        ),
        difference=difference,
    )


def undeclared_status(snapshot: dict) -> Status:
    """The status for a live repository nothing declares.

    Separate from `repository_status` because it is the opposite question, and because
    it is the finding the first run of this inventory exists to produce: on an account
    nobody has ever declared, every repository lands here.

    `adopt` rather than `create`: the resource exists and is being brought under
    management as it is. `warning` rather than `pending`: there is nothing to change
    until somebody writes down what it should look like.

    Args:
        snapshot: The snapshot of the undeclared repository.

    Returns:
        A Status.

    Example:
        >>> undeclared_status({"topics": [], "license": None, "private": False}).action
        'adopt'
    """
    notes = []
    if not snapshot.get("license"):
        notes.append("no licence")
    if not snapshot.get("topics"):
        notes.append("no topics")
    if snapshot.get("private"):
        notes.append("private")

    detail = f" Currently: {', '.join(notes)}." if notes else ""

    return Status(
        action="adopt",
        status="warning",
        reason=(
            "Present on the account and not declared, so nothing states how it should be "
            f"configured.{detail} Add it to the configuration to bring it under management."
        ),
        difference=[],
    )


def status_as_dict(status: Status) -> dict[str, Any]:
    """The status as a plain mapping, for a report."""
    return {
        "action": status.action,
        "status": status.status,
        "reason": status.reason,
        "difference": [item.as_dict() for item in status.difference],
    }
