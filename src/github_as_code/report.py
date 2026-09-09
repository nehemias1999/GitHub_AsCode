"""Evidence writing.

Two artefacts, each answering a different question. A plan report answers "what was
about to happen", and is the thing a reviewer approves. A Markdown summary answers "can
a person read this without a JSON viewer", and is what gets attached to a change ticket.

Everything written here passes through `remove_sensitive_values` first, and that walk
masks every string it copies with `protect_secrets_in_text`. Two layers, because they
see different things: one matches the NAME of a property, which catches a weak password
whose value looks like nothing, and the other matches the VALUE, which catches a token
embedded in a URL under a name like `apiBaseUrl` that no pattern would ever flag.

Both live at the writer rather than at each call site. A report is the artefact most
likely to be pasted into a ticket or a chat window, and a call site added next year
would otherwise reintroduce the leak without anybody noticing.
"""

import datetime
import getpass
import hashlib
import json
import re
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from github_as_code import configuration, schema
from github_as_code import plan as plan_module

# Property names whose values are replaced before a report is written. Matching is on
# the name, not the value, so a credential is redacted even when it does not look like
# one.
#
# The pattern is built in two halves, because a single unanchored alternation is wrong
# in both directions at once.
#
# Long, unambiguous tokens match anywhere in the name. Case-insensitivity is what makes
# these cover camelCase too: 'sshkey' matches 'sshKey'.
_SENSITIVE_NAME_FRAGMENT = "|".join(
    (
        "password", "passwd", "pwd", "passphrase",
        "secret", "credential", "token", "authorization",
        "apikey", "api_key", "accesskey", "privatekey", "private_key", "sshkey",
        "signingkey", "keymaterial",
        "connectionstring", "connstr", "signature",
    )
)

# Short tokens that are also common substrings of innocent words. These match only as a
# whole word or a whole underscore/dash-delimited segment.
#
# 'pat' is the reason this split exists. Unanchored, it matched 'areaPaths',
# 'reportPath', 'patch' and 'compatible' - so an inventory report silently replaced the
# very data it exists to carry with the redaction marker. Redaction that destroys
# evidence is not failing safe; it is failing quietly, which is worse.
#
# IT HAPPENED AGAIN, with 'token' in the list above rather than 'pat' below.
# repo-inventory built a block of evidence about the token's SHAPE - classic or
# fine-grained, how many days until it expires - named it 'token', and the walk replaced
# the whole object with "[redacted]". Nothing in it was secret; the value never went
# near it. The report promised the expiry and then destroyed the only durable record of
# it.
#
# The first case taught "anchor short fragments". The second adds that a NAME can be
# sensitive-looking while the thing under it is evidence, and the mechanism cannot tell
# the difference. The caller carries the burden: name the field for what it holds rather
# than for the subject it concerns, and assert in a test that it survives.
_SENSITIVE_NAME_SEGMENT = "|".join(("pat", "key", "sas", "cert", "auth", "bearer"))

SENSITIVE_PROPERTY_PATTERN = re.compile(
    f"({_SENSITIVE_NAME_FRAGMENT}|(?:^|[_-])(?:{_SENSITIVE_NAME_SEGMENT})(?:[_-]|$))",
    re.IGNORECASE,
)

# Value shapes that carry a secret regardless of the property name holding them.
#
# Name-based redaction cannot see these, and that is not a hypothetical gap. A URL of
# the form https://user:TOKEN@host stored under the name 'apiBaseUrl' looks innocent -
# while 'credentialsId', which is only a reference and no secret at all, IS redacted
# because its name contains 'credential'. The name layer protects the harmless field and
# passes the dangerous one.
#
# Free text is the other half of the same hole: a reason or a failure message is a string
# under a name no pattern would ever flag.
SECRET_VALUE_RULES = (
    # URL userinfo. The host is kept: it is the diagnostically useful part, and a reason
    # that says which repository disagrees is the point of the message.
    (re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^/\s@\"']+@", re.IGNORECASE), r"\1[redacted]@"),
    # Bearer, which is the scheme THIS repository authenticates with. Its absence was
    # the gap that mattered: both rules here arrived with a port from a project that
    # uses Basic, so the layer whose entire purpose is catching "a token that reached a
    # message by a route nobody enumerated" was blind to the only scheme in use.
    #
    # \S+ rather than a token-shaped pattern, on purpose. Whatever follows Bearer is a
    # credential regardless of how it looks, and a rule matching only known prefixes
    # would miss the next format GitHub introduces.
    (re.compile(r"\bBearer\s+\S+", re.IGNORECASE), "Bearer [redacted]"),
    # A bare GitHub token, under no header and no property name. Both other layers miss
    # this. Two shapes: the five classic gh*_ prefixes, and github_pat_ for
    # fine-grained - which is the type this repository recommends, and which carries
    # underscores in its body, so it needs its own character class.
    (re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}"), "[redacted-token]"),
    (re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,}"), "[redacted-token]"),
    # A Basic credential. Kept even though nothing here sends one: a proxy or an SSO
    # gateway in front of the API can put one in a message, and the rule costs nothing.
    (re.compile(r"\bBasic\s+[A-Za-z0-9+/]{8,}={0,2}", re.IGNORECASE), "Basic [redacted]"),
)


@dataclass(frozen=True)
class ReportPaths:
    """Where the two artefacts of one run were written."""

    json_path: Path
    markdown_path: Path


def protect_secrets_in_text(text: str | None) -> str | None:
    """Return text with credential-shaped values masked.

    Masks by VALUE, which is the complement of `remove_sensitive_values` masking by
    property name. Use it for any string that reaches a report, a Markdown summary or
    the console.

    The host of a URL is preserved deliberately. A message that says which repository
    was contacted is worth having; the userinfo in front of it never is.

    Args:
        text: Text to mask. None or empty is returned unchanged.

    Returns:
        The masked string.

    Example:
        >>> protect_secrets_in_text("Authorization: Bearer abc123")
        'Authorization: Bearer [redacted]'
    """
    if not text:
        return text
    masked = text
    for pattern, replacement in SECRET_VALUE_RULES:
        masked = pattern.sub(replacement, masked)
    return masked


def remove_sensitive_values(value: Any, replacement: str = "[redacted]", depth: int = 12) -> Any:
    """Return a copy of a structure with sensitive property values replaced.

    A property whose NAME looks like a credential is replaced with a fixed marker;
    everything else is copied. Name matching rather than value matching is deliberate: a
    weak password does not look like a secret, but its property name always does.

    Every string in the evidence also passes the value masker. Doing it here rather than
    at each call site is the same reasoning that put name-based redaction at the writer.

    Depth is capped so a cyclic or pathologically nested structure cannot hang the
    writer.

    Args:
        value: Structure to sanitise. The input is not modified.
        replacement: Marker written in place of a sensitive value.
        depth: Remaining recursion depth.

    Returns:
        A sanitised copy.

    Example:
        >>> remove_sensitive_values({"credential": "EXAMPLE"})["credential"]
        '[redacted]'

    (The example deliberately does not spell out a credential-shaped key beside a
    literal value on one line. scripts/Test-NoSensitiveData.ps1 has a rule for exactly
    that shape, and it fired on the first version of this docstring. The guard cannot
    tell an illustration from the real thing, and AGENTS.md section 4 says to rename
    the code rather than teach the guard an exemption.)
    """
    if value is None:
        return None
    if depth <= 0:
        return "[depth limit reached]"
    if isinstance(value, str):
        return protect_secrets_in_text(value)
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, dict):
        copy = {}
        for key, item in value.items():
            if SENSITIVE_PROPERTY_PATTERN.search(str(key)):
                copy[str(key)] = replacement
                continue
            copy[str(key)] = remove_sensitive_values(item, replacement, depth - 1)
        return copy
    if isinstance(value, list | tuple):
        return [remove_sensitive_values(item, replacement, depth - 1) for item in value]
    return value


def write_text(path: Path | str, content: str) -> Path:
    """Write text as UTF-8 with no byte order mark.

    The PowerShell version needed a .NET call for this, because `Set-Content -Encoding
    UTF8` writes a BOM on Windows PowerShell 5.1 and a JSON document starting with one
    is rejected by strict parsers - Python among them, which answers "Unexpected UTF-8
    BOM". A report nobody can parse is not evidence.

    Python writes no BOM, so the hazard does not survive the port. The reason is kept
    because the ledger of why a thing was done is worth more than the thing.

    Newlines are written as-is rather than translated, so a report is byte-identical on
    either platform - which is what makes an offline comparison between the two
    implementations meaningful.

    Args:
        path: File to write. The parent directory is created if needed.
        content: Text to write.

    Returns:
        The path written.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8", newline="")
    return destination


def report_path(
    repository_root: Path | str,
    module: str,
    command: str,
    explicit: str | None = None,
    now: datetime.datetime | None = None,
    unique: str | None = None,
) -> Path:
    """Decide where a run writes its report.

    An explicit path wins; otherwise a generated one under `artifacts/reports`. The
    generated name is UTC and unique, not merely precise: local time in the name while
    the content records UTC meant a directory's lexicographic order was not its
    chronological one, and in the autumn clock change two runs an hour apart landed on
    the same name. One second of granularity collided on its own anyway - two runs of
    the same command in the same second overwrote each other, and the writer truncates
    without a word.

    Args:
        repository_root: Root that the generated path is relative to.
        module: Automation name, used in the file name.
        command: Verb, used in the file name.
        explicit: Explicit override. When given, nothing is generated.
        now: Injected for tests.
        unique: Injected for tests.

    Returns:
        The path to write to.

    Example:
        >>> report_path(".", "repo-inventory", "plan", explicit="out.json").name
        'out.json'
    """
    if explicit:
        return configuration.resolve_path(explicit, repository_root)
    moment = now or datetime.datetime.now(datetime.UTC)
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    suffix = unique or uuid.uuid4().hex[:6]
    name = f"{module}-{command}-{stamp}Z-{suffix}.json"
    return Path(repository_root) / "artifacts" / "reports" / name


def provenance(
    command: str,
    declaration_path: str,
    declaration_text: str,
    scope: str,
    repository_root: Path | str,
    tool_version: str,
    used_template: bool = False,
    correlation_id: str | None = None,
) -> dict:
    """The facts a report needs for somebody to reproduce the run.

    Four things were missing from the inherited report and each made a specific question
    unanswerable. Who and where. Which code - no tool version and no repository commit,
    so "this report says X" could not be tied to the logic that read it. Which
    declaration - the path was recorded, but the active declaration is excluded from
    version control, so the path identifies a file that may have changed since; a
    fingerprint of the content does identify it. And which scope, which is the dangerous
    one: "pending 0" reads as "everything is aligned" when it can equally mean "only one
    repository was examined".

    `schemaEngine` is pinned to the one engine, per ADR 0007. The field stays for
    report-shape parity, and a test asserts it never varies.

    `runBy` and `runOn` come from `getpass` and `socket` rather than from USERNAME and
    COMPUTERNAME, which is one of the two genuinely Windows-bound things ADR 0006 names.
    Both are best effort: a container with no passwd entry is a legitimate place to run
    this, and a report with an empty field is better than no report.

    Args:
        command: Verb that produced the report.
        declaration_path: Path of the declaration that was read.
        declaration_text: Content of that declaration, fingerprinted rather than stored.
        scope: What the run was restricted to, or 'all'.
        repository_root: Root used to resolve the repository commit.
        tool_version: Version this product states for itself.
        used_template: True when the run read the versioned TEMPLATE rather than the
            active declaration. It belongs in the report because it changes what the
            report is ABOUT: a plan built from the template describes an example, not an
            estate.
        correlation_id: Injected for tests.

    Returns:
        A mapping ready to merge into a report detail.

    Example:
        >>> provenance("plan", "d.json", "", "all", ".", "0.1.0")["schemaEngine"]
        'builtin'
    """
    fingerprint = ""
    if declaration_text:
        # Newlines normalised and trailing ones trimmed before hashing, so a checkout
        # with CRLF and one with LF fingerprint the same declaration identically. That
        # matters more than it looks: the fingerprint is the FIRST thing ADR 0006's
        # deletion trigger compares between the two implementations, and a fingerprint
        # that varied by platform would make every later comparison meaningless.
        normalized = declaration_text.replace("\r\n", "\n").rstrip("\n")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        fingerprint = f"sha256:{digest}"

    # Best effort, and silent when it fails. A checkout with no .git - a tarball, a
    # vendored copy - is a legitimate way to run this.
    commit = ""
    try:
        head = Path(repository_root) / ".git" / "HEAD"
        if head.is_file():
            head_text = head.read_text(encoding="utf-8").strip()
            if head_text.startswith("ref: "):
                ref = Path(repository_root) / ".git" / head_text[5:].strip()
                if ref.is_file():
                    commit = ref.read_text(encoding="utf-8").strip()
            else:
                commit = head_text
    except OSError:
        commit = ""

    try:
        run_by = getpass.getuser()
    except Exception:  # noqa: BLE001 - any failure here means "unknown", not a crash
        run_by = ""
    try:
        run_on = socket.gethostname()
    except OSError:
        run_on = ""

    return {
        "command": command,
        "toolVersion": tool_version,
        "correlationId": correlation_id or str(uuid.uuid4()),
        "runBy": run_by,
        "runOn": run_on,
        "repositoryCommit": commit,
        "declarationPath": declaration_path,
        "declarationFingerprint": fingerprint,
        "declarationIsTemplate": bool(used_template),
        "schemaEngine": schema.ENGINE,
        "scope": scope or "all",
    }


def format_markdown_cell(text: Any) -> str:
    """Return text safe to place in one cell of a Markdown table.

    A plan carries text this tool did not write: a repository name, a description and a
    reason all come from the account being inspected. Three things go wrong when that is
    pasted into a table unescaped. A pipe ends the cell, so the row grows a column. A
    newline ends the row, so one value silently becomes two rows. And a bracket pair
    followed by a parenthesis is a link, so a value can inject one into whatever renders
    the summary on a ticket.

    None of that is a route to a secret - it is a route to a report that is wrong or that
    carries something nobody wrote.

    Args:
        text: Cell text. None becomes an empty cell rather than the word None.

    Returns:
        The escaped single-line string.

    Example:
        >>> format_markdown_cell("a | b")
        'a \\\\| b'
    """
    if text is None:
        return ""
    cell = str(text)
    # Backslash first, because doing it later would escape the backslashes the other
    # steps just added.
    cell = cell.replace("\\", "\\\\")
    cell = cell.replace("|", "\\|")
    cell = cell.replace("[", "\\[").replace("]", "\\]")
    cell = cell.replace("<", "&lt;").replace(">", "&gt;")
    # A cell is one line. CRLF first, so a Windows newline does not leave a stray
    # carriage return behind for the next step to turn into a second space.
    cell = cell.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return cell


def format_report_markdown(report: dict) -> str:
    """Render a report mapping as Markdown.

    Pure function, so the rendering is covered by a test without touching the file
    system. Operations are grouped by status, with the ones needing attention first,
    because that is the order a reviewer reads in.

    Args:
        report: Sanitised report mapping.

    Returns:
        The Markdown document as a single string.

    Example:
        >>> format_report_markdown({"module": "m", "command": "c", "target": "t",
        ...     "generatedAt": "x", "summary": {}, "operations": []}).splitlines()[0]
        '# m: c - t'
    """
    lines = [
        f"# {report['module']}: {report['command']} - {report['target']}",
        "",
        f"Generated at {report['generatedAt']} (UTC).",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "| --- | --- |",
    ]
    for name, count in report["summary"].items():
        lines.append(f"| {name} | {count} |")
    lines += ["", "## Operations", ""]

    operations = report["operations"]
    if not operations:
        lines.append("No operations were produced.")
    else:
        for status in ("blocked", "warning", "pending", "protected", "ok"):
            items = [item for item in operations if item["status"] == status]
            if not items:
                continue
            lines += [
                f"### {status} ({len(items)})",
                "",
                "| Resource | Name | Action | Reason |",
                "| --- | --- | --- | --- |",
            ]
            for item in items:
                # All four cells escaped, not just the reason. Three of them carry text
                # the inspected account chooses, so a pipe or a newline in any of them
                # breaks the table.
                lines.append(
                    "| {} | {} | {} | {} |".format(
                        format_markdown_cell(item["resource"]),
                        format_markdown_cell(item["name"]),
                        format_markdown_cell(item["action"]),
                        format_markdown_cell(item["reason"]),
                    )
                )
            lines.append("")

    return "\n".join(lines)


def write_report(
    plan: plan_module.Plan,
    path: Path | str,
    module: str,
    detail: Any = None,
) -> ReportPaths:
    """Write a plan report as JSON, plus a Markdown sibling.

    The JSON file is the machine-readable record; the Markdown file next to it is what a
    person reads or attaches to a ticket. Both are written from the same sanitised
    object, so they cannot disagree.

    Reports belong under `artifacts/`, which is excluded from version control: they
    describe one run of one account and are not part of the declared state.

    Args:
        plan: The plan.
        path: Destination for the JSON report. The Markdown file replaces the extension.
        module: Name of the automation that produced the report.
        detail: Optional extra mapping to embed, for example the inventory counts.

    Returns:
        The two paths written.
    """
    report = {
        "module": module,
        "command": plan.command,
        "target": plan.target,
        "generatedAt": plan.generated_at,
        "summary": plan_module.plan_summary(plan),
        "operations": [operation.as_dict() for operation in plan.operations],
    }
    if detail is not None:
        report["detail"] = detail

    sanitized = remove_sensitive_values(report)

    json_path = Path(path)
    # sort_keys is deliberately off: the report's key order is part of its shape, and
    # the two implementations are compared field by field rather than as text.
    write_text(json_path, json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n")

    markdown_path = json_path.with_suffix(".md")
    write_text(markdown_path, format_report_markdown(sanitized) + "\n")

    return ReportPaths(json_path=json_path, markdown_path=markdown_path)


def start_run_log(
    repository_root: Path | str,
    module: str,
    command: str,
    now: datetime.datetime | None = None,
    unique: str | None = None,
) -> Path | None:
    """Open the transcript for one run and return its path.

    Progress used to exist only on the console, so closing the window ended the only
    account of what a run did. The report holds the conclusions and nothing about how
    they were reached: which pages were walked, the message behind every unreadable
    repository, and the one that matters most - that the run used the versioned TEMPLATE
    rather than the active declaration.

    Failing to open it is not fatal, because a run that reports correctly without a
    transcript is better than no run at all.

    Args:
        repository_root: Root under which artifacts/logs lives.
        module: Automation name, used in the file name.
        command: Verb, used in the file name.
        now: Injected for tests.
        unique: Injected for tests.

    Returns:
        The transcript path, or None when one could not be opened.
    """
    try:
        directory = Path(repository_root) / "artifacts" / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        moment = now or datetime.datetime.now(datetime.UTC)
        stamp = moment.strftime("%Y%m%d-%H%M%S")
        suffix = unique or uuid.uuid4().hex[:6]
        path = directory / f"{module}-{command}-{stamp}Z-{suffix}.log"
        add_run_log_line(path, "info", f"run started: {module} {command}")
        return path
    except OSError:
        return None


def add_run_log_line(path: Path | None, level: str, message: str) -> None:
    """Append one line to a run transcript.

    Every line carries a UTC timestamp and a level, so the transcript can be read long
    after the console it was echoed to is gone, and so the failures can be picked out of
    a long run without reading all of it.

    Masked the same way the console funnel is. A transcript is written to disk and lives
    longer than a scrollback buffer, so it is the last place a credential should settle.

    Never raises. A transcript that cannot be written must not take the run down with
    it - the report is the artefact that matters.

    Args:
        path: Transcript path. None is ignored, which is how a run with no transcript
            keeps working.
        level: 'info' or 'warning', matching the console funnel.
        message: Line to write.
    """
    if path is None:
        return
    try:
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{stamp} [{level}] {protect_secrets_in_text(message)}\n"
        with Path(path).open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
    except OSError:
        return
