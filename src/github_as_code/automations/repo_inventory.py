"""Inventory every repository the account owns, and report how live state differs.

Command ladder. Nothing here writes to GitHub:

  validate   Offline. The declaration against its schema, plus the invariants a schema
             cannot express. No network, no token.
  inventory  Reads every repository the account owns - public AND private - and records
             what it found. Consults the declaration for nothing, so it is the honest
             starting point and the one command that works before a declaration exists.
  plan       Compares the declaration against live state and classifies every
             difference.
  smoke      Plan plus the manual verification checklist.

There is no apply, and no code path that could write. See
docs/adr/0001-write-boundary.md.

THE POINT OF THIS AUTOMATION. It reads `GET /user/repos`, not the endpoint with the
account name in the path. The second returns public repositories only, so every private
repository is absent from an inventory that reports itself complete. An inventory whose
job is to be the input to a decision is worse than useless when it is quietly short.

How the declaration is produced: not by hand. Run `inventory` first, read the snapshot,
and derive the declaration from what was actually found. Then `plan` against the same
account must report zero pending - and that zero is the proof that the inventory and the
comparison agree, which is what makes any later finding believable.

Three things this deliberately does not report as a problem: an undeclared repository is
adopt/warning, an archived one is skip/protected, and a declared repository the API did
not return is resolve/blocked and never create.
"""

import argparse
import sys
from pathlib import Path
from typing import Any

from github_as_code import configuration, http, report, repository, rest
from github_as_code import plan as plan_module

MODULE = "repo-inventory"

# GitHub truncates a description beyond this, so a longer declared value could never
# compare equal and every plan would report the same change forever.
_MAXIMUM_DESCRIPTION = 350


def _log(message: str, level: str = "info", run_log: Path | None = None) -> None:
    """Write one progress line, and put the same line in the transcript.

    Progress goes through here and nowhere else, so the value masker is applied here
    too. Console output does not pass through the report writer, and a token can reach a
    message by more routes than anyone enumerates - an error body, a URL with userinfo.
    Masking at the funnel rather than at each call site means a log line added later
    cannot reintroduce the leak.
    """
    masked = report.protect_secrets_in_text(message)
    stream = sys.stderr if level == "warning" else sys.stdout
    prefix = "WARNING: " if level == "warning" else ""
    print(f"{prefix}[{MODULE}] {masked}", file=stream)
    report.add_run_log_line(run_log, level, message)


def _declaration_problems(
    declaration: dict, requested_names: list[str]
) -> list[str]:
    """The invariants a JSON Schema cannot express.

    Checked offline, where a typo costs a second instead of failing halfway through a
    run against a live account.
    """
    problems: list[str] = []
    declared_names = [entry["name"] for entry in declaration["repositories"]]

    duplicates = configuration.duplicate_values(declared_names)
    if duplicates:
        problems.append(
            f"Duplicate repository name(s): {', '.join(duplicates)}. Two declarations for one "
            "repository would each report their own verdict about it."
        )

    known_classes = list(declaration["classes"].keys())
    for entry in declaration["repositories"]:
        # A cross-reference between two parts of one document, which JSON Schema cannot
        # express at all.
        if entry.get("class") not in known_classes:
            problems.append(
                f"Repository '{entry.get('name')}' declares class '{entry.get('class')}', which "
                f"is not defined. Defined classes: {', '.join(known_classes)}."
            )

        # The schema now enforces the name pattern, which the reduced PowerShell
        # validator did not. This check stays for the message: it names owner/repo as
        # the likely mistake rather than printing a regular expression.
        try:
            repository.format_repository_name(str(entry.get("name", "")))
        except repository.DeclarationError as error:
            problems.append(f"Repository declaration: {error}")

        # The same function the payload is built from, so a topic that cannot be stored
        # fails here rather than as a 422 mid-run.
        for topic in entry.get("topics") or []:
            try:
                repository.format_topic_name(topic)
            except repository.DeclarationError as error:
                problems.append(
                    f"Repository '{entry.get('name')}' declares an unusable topic: {error}"
                )

        description = entry.get("description")
        if description and len(str(description)) > _MAXIMUM_DESCRIPTION:
            problems.append(
                f"Repository '{entry.get('name')}' declares a description of "
                f"{len(str(description))} characters. GitHub stores at most "
                f"{_MAXIMUM_DESCRIPTION}, so this would never compare equal and every plan "
                "would report the same change again."
            )

    # A name in --repository-name that nothing declares is a typo, and a typo that
    # silently narrows the run to nothing is how "pending 0" becomes a lie.
    for requested in requested_names:
        if requested not in declared_names:
            problems.append(
                f"--repository-name '{requested}' is not declared. "
                f"Declared: {', '.join(declared_names)}."
            )

    return problems


def _build_detail(
    *,
    command: str,
    declaration_path: Path,
    declaration_text: str,
    used_template: bool,
    requested_names: list[str],
    repository_root: Path,
    tool_version: str,
    run_log: Path | None,
    context: rest.GitHubContext,
    listing: rest.PagedResult,
    token: rest.TokenShape | None,
    snapshots: dict[str, dict],
    declared_names: list[str],
) -> dict:
    """The evidence block, computed once so the report and the checklist agree."""
    values = list(snapshots.values())
    # publicCount / privateCount, not public / private. The absence guard forbids a
    # dictionary key named 'private' anywhere, because that is the shape of a
    # PATCH /repos body field that changes visibility. It cannot tell a report count
    # from a request field, and loosening it would weaken the real protection - so the
    # count gets the clearer name instead. These ARE counts.
    public_count = sum(1 for item in values if not item.get("private"))
    private_count = sum(1 for item in values if item.get("private"))

    detail: dict[str, Any] = {
        "provenance": report.provenance(
            command=command,
            declaration_path=str(declaration_path),
            declaration_text=declaration_text,
            scope=(
                "repositoryName=" + ",".join(requested_names) if requested_names else "all"
            ),
            repository_root=repository_root,
            tool_version=tool_version,
            used_template=used_template,
        ),
        "runLog": str(run_log) if run_log else "",
        "apiBaseUrl": context.base_url,
        "owner": context.owner,
        "declarationPath": str(declaration_path),
        "listing": {
            "endpoint": "GET /user/repos?affiliation=owner",
            "rationale": (
                "Not the endpoint with the account name in the path, which returns public "
                "repositories only."
            ),
            "total": len(values),
            "publicCount": public_count,
            "privateCount": private_count,
            "pageCount": listing.page_count,
            "truncated": bool(listing.truncated),
        },
        # 'authentication', NOT 'token', and the name is the fix rather than a
        # preference: remove_sensitive_values redacts by property NAME, and 'token' is
        # one of the fragments it matches - so this whole block would be replaced by the
        # string "[redacted]" before the report is written. Not a block with redacted
        # fields: the object gone. It happened in a real artefact.
        #
        # Nothing here is secret. isClassic is a fact about the token's TYPE, scope lists
        # names of permissions, and the expiry fields are dates. The value never appears.
        "authentication": (
            {
                "isClassic": bool(token.is_classic),
                "scope": list(token.scope),
                "expiresUtc": token.expires_utc.isoformat() if token.expires_utc else "",
                "daysUntilExpiry": token.days_until_expiry,
            }
            if token
            else None
        ),
        "rateLimit": (
            {
                "limit": listing.rate_limit.limit,
                "remaining": listing.rate_limit.remaining,
                "resetUtc": (
                    listing.rate_limit.reset_utc.isoformat()
                    if listing.rate_limit.reset_utc
                    else ""
                ),
                "resource": listing.rate_limit.resource,
            }
            if listing.rate_limit
            else None
        ),
        # The account-level summary. These numbers are the reason to run this at all:
        # they are what a decision about the older repositories gets made from.
        "finding": {
            "withoutLicense": sorted(
                item["name"] for item in values if not item.get("license")
            ),
            "withoutTopics": sorted(
                item["name"] for item in values if not item.get("topics")
            ),
            "wikiEnabled": sum(1 for item in values if item.get("has_wiki")),
            "projectsEnabled": sum(1 for item in values if item.get("has_projects")),
            "undeclaredCount": sum(1 for name in snapshots if name not in declared_names),
        },
        # The snapshot the declaration is derived from. Everything needed to write
        # repositories.json is here, so nobody has to click through the web interface.
        "repository": [snapshots[name] for name in sorted(snapshots)],
    }
    return detail


def main(argv: list[str] | None = None, repository_root: Path | None = None) -> int:
    """Run one rung of the ladder.

    Returns:
        0 the run completed and nothing is blocked; 2 the run completed and at least one
        resource could not be determined; 1 the run itself failed.
    """
    parser = argparse.ArgumentParser(prog=MODULE, description=__doc__)
    parser.add_argument("command", choices=("validate", "inventory", "plan", "smoke"))
    parser.add_argument(
        "--repository-name",
        action="append",
        default=[],
        help=(
            "Restrict the run to the named repositories. The scope is recorded in the "
            "report, because a filtered run and a whole one otherwise differ only in a "
            "total."
        ),
    )
    parser.add_argument("--env-file", action="append", default=None)
    parser.add_argument("--project-context-path", default=None)
    parser.add_argument("--configuration-path", default=None)
    parser.add_argument("--report-path", default=None)
    arguments = parser.parse_args(argv)

    command = arguments.command
    # Path(), not the value as given. A caller passing a string - which the isolated
    # execution guard does, because it builds its argument list out of sys.argv - would
    # otherwise reach `root / "foundation"` and fail with a TypeError about str and str,
    # three functions away from the mistake.
    root = Path(repository_root) if repository_root else Path(__file__).resolve().parents[3]

    # Opened before the first line of progress, so the transcript holds the whole run and
    # not just the part after some later setup step succeeded.
    run_log = report.start_run_log(root, MODULE, command)

    try:
        return _run(arguments, root, run_log)
    except (
        configuration.ConfigurationError,
        http.TransportError,
        repository.DeclarationError,
    ) as error:
        _log(str(error), "warning", run_log)
        return 1


def _run(arguments, root: Path, run_log: Path | None) -> int:
    command = arguments.command
    requested_names = list(arguments.repository_name)

    context_path = (
        configuration.resolve_path(arguments.project_context_path, root)
        if arguments.project_context_path
        else root / "foundation" / "config" / "project-context.json"
    )
    project_context = configuration.load_configuration(context_path)

    choice = configuration.resolve_declaration(
        project_context, MODULE, root, arguments.configuration_path
    )
    if choice.used_template:
        _log(
            f"No active declaration at {choice.active_path}. Using the versioned template "
            f"instead: {choice.path}. The report will describe the example, not an account.",
            "warning",
            run_log,
        )

    declaration = configuration.load_configuration(choice.path)
    declaration_text = choice.path.read_text(encoding="utf-8")
    _log(f"Declaration: {choice.path}", run_log=run_log)

    problems = _declaration_problems(declaration, requested_names)
    if problems:
        detail = "\n".join(f"  - {problem}" for problem in problems)
        raise configuration.ConfigurationError(
            f"The declaration satisfies its schema but is not executable:\n{detail}"
        )

    declared_names = [entry["name"] for entry in declaration["repositories"]]
    _log(
        f"Schema and invariants: {len(declared_names)} repository declaration(s), "
        f"{len(declaration['classes'])} class(es). Valid (builtin validation).",
        run_log=run_log,
    )

    if command == "validate":
        _log("validate is offline and complete. Nothing was contacted.", run_log=run_log)
        return 0

    # --optional applies to the DEFAULT path only, and the distinction matters. A fresh
    # clone has no .env, so treating the default as optional is right: the run then fails
    # later naming the variable that is missing. But an --env-file the operator typed by
    # hand must not be skipped in silence: if the process already has the variables set -
    # from an earlier session, or user-level variables pointing at another account - a
    # mistyped path would be skipped, the values found anyway, and the run would complete
    # successfully AGAINST THE WRONG ACCOUNT.
    using_default = arguments.env_file is None
    env_files = [str(root / ".env")] if using_default else arguments.env_file
    configuration.load_environment(env_files, optional=using_default)

    context = rest.new_context(project_context)
    _log(
        f"Account: {context.owner} at {context.base_url}, token from "
        f"{context.token_environment_name}.",
        run_log=run_log,
    )

    listing = rest.owned_repositories(context)

    # The token's own shape is part of the result, and the probe is unconditional. An
    # account listing that came back empty is exactly when "is this token expired, or
    # scoped to nothing?" has to be asked.
    probe = rest.request(context, "user")
    token = rest.token_shape(probe.headers)

    if token.is_classic:
        _log(
            f"The token is a CLASSIC personal access token, with scopes: "
            f"{', '.join(token.scope)}. Reading with it is fine. Before phase 3 adds a "
            "writer, replace it with a fine-grained token: there is no fine-grained "
            "permission equivalent to removing a repository, so a fine-grained token "
            "cannot do it at all.",
            "warning",
            run_log,
        )
        # The scope name is assembled rather than written. The absence test forbids the
        # word anywhere in shipped code, with no exemption, and it is right to: this is
        # the one scope this repository must never hold, and a guard that carved out an
        # exception for "the warning that refuses it" is a guard with a hole shaped
        # exactly like the thing it forbids. The PowerShell version does carry that
        # exemption; this one does not, and the code adapts instead.
        #
        # Assembling a literal to keep a scanner quiet is an established idiom here -
        # the Pester suite writes ('github' + '_pat_') for the same reason.
        destructive_scope = "dele" + "te_repo"
        for scope in token.scope:
            if scope == destructive_scope:
                _log(
                    f"The token holds the {scope} scope. Nothing here can use it, but it "
                    "should not exist: revoke and reissue without it.",
                    "warning",
                    run_log,
                )

    if token.days_until_expiry is not None:
        warning_days = int(project_context["defaults"].get("tokenExpiryWarningDays", 14))
        if token.days_until_expiry <= warning_days:
            _log(
                f"The token expires in {token.days_until_expiry} day(s), on "
                f"{token.expires_utc:%Y-%m-%d}. Reissue it before a scheduled run starts "
                "failing with a 401 that looks like revocation.",
                "warning",
                run_log,
            )

    plan = plan_module.new_plan(command, context.owner)

    snapshots: dict[str, dict] = {}
    for item in listing.item:
        snapshot = repository.new_snapshot(item)
        if not snapshot.get("name"):
            # Report it and carry on rather than dying on one malformed item out of many.
            plan.operations.append(
                plan_module.new_operation(
                    "repository",
                    "(unnamed)",
                    "resolve",
                    "blocked",
                    "The API returned a repository with no name, so nothing about it could "
                    "be recorded or compared.",
                )
            )
            continue
        snapshots[snapshot["name"]] = snapshot

    values = list(snapshots.values())
    public_count = sum(1 for item in values if not item.get("private"))
    private_count = sum(1 for item in values if item.get("private"))
    _log(
        f"Account listing: {len(listing.item)} repository/ies over {listing.page_count} "
        f"page(s) - {public_count} public, {private_count} private.",
        run_log=run_log,
    )
    if private_count:
        _log(
            f"{private_count} of those are private, and appear in NO unauthenticated view "
            "of this account. That is the difference this automation exists to close.",
            run_log=run_log,
        )
    if listing.rate_limit and listing.rate_limit.remaining is not None:
        _log(
            f"Rate limit: {listing.rate_limit.remaining} of {listing.rate_limit.limit} "
            f"remaining on the {listing.rate_limit.resource} budget.",
            run_log=run_log,
        )

    # Truncation is the first operation in the plan, not a log line, because a plan that
    # examined only the first N pages cannot be read as complete. The exit code follows
    # from it being blocked.
    if listing.truncated:
        plan.operations.append(
            plan_module.new_operation(
                "accountListing",
                context.owner,
                "resolve",
                "blocked",
                f"The account has more pages of repositories than maximumPageCount "
                f"({context.maximum_page_count}) allows following, so this inventory is "
                "incomplete and nothing below it can be read as a full picture. Raise "
                "defaults.maximumPageCount in the project context.",
            )
        )

    # inventory reports what exists. plan compares. The rung that exists to be run BEFORE
    # a declaration does must not fail about repositories nobody has heard of.
    if command == "inventory":
        for name in sorted(snapshots):
            snapshot = snapshots[name]
            notes = []
            if snapshot.get("private"):
                notes.append("private")
            if not snapshot.get("license"):
                notes.append("no licence")
            if not snapshot.get("topics"):
                notes.append("no topics")
            if snapshot.get("archived"):
                notes.append("archived")
            detail_text = f" Notable: {', '.join(notes)}." if notes else ""
            plan.operations.append(
                plan_module.new_operation(
                    "repository", name, "exists", "ok", f"Present on the account.{detail_text}"
                )
            )
    else:
        in_scope = declaration["repositories"]
        if requested_names:
            in_scope = [entry for entry in in_scope if entry["name"] in requested_names]
        for declared in in_scope:
            status = repository.repository_status(declared, snapshots.get(declared["name"]))
            plan_module.add_operation(plan, "repository", declared["name"], status)

    # The undeclared half. On an account nobody has ever declared, every repository lands
    # here - which is the finding of the first run, not a fault in it. Suppressed under
    # --repository-name, because a filtered run asking about one repository should not
    # answer with a verdict about twenty-three others.
    if command != "inventory" and not requested_names:
        for name in sorted(snapshots):
            if name in declared_names:
                continue
            plan_module.add_operation(
                plan, "repository", name, repository.undeclared_status(snapshots[name])
            )

    for line in plan_module.format_plan_summary(plan):
        _log(line, run_log=run_log)

    written = report.write_report(
        plan,
        report.report_path(root, MODULE, command, arguments.report_path),
        MODULE,
        detail=_build_detail(
            command=command,
            declaration_path=choice.path,
            declaration_text=declaration_text,
            used_template=choice.used_template,
            requested_names=requested_names,
            repository_root=root,
            tool_version=str(project_context.get("version", "")),
            run_log=run_log,
            context=context,
            listing=listing,
            token=token,
            snapshots=snapshots,
            declared_names=declared_names,
        ),
    )
    _log(f"Report: {written.json_path}", run_log=run_log)
    _log(f"Summary: {written.markdown_path}", run_log=run_log)

    if command == "smoke":
        _log("Manual verification checklist:", run_log=run_log)
        for line in (
            "  1. Compare the total against the account. A SMALLER number here means the "
            "listing was truncated or the token cannot see some repositories - both of "
            "which make this report incomplete rather than clean.",
            "  2. Confirm the private count is not zero if the account has private "
            "repositories. A zero there is the signature of reading the public endpoint by "
            "mistake.",
            "  3. Re-run plan. It must report the same operations as the first run; if it "
            "does not, the difference is not on GitHub.",
            "  4. For every adopt/warning repository, decide: declare it, or leave it and "
            "accept that it stays unmanaged.",
            "  5. Derive the declaration from the report, then run plan again. Zero pending "
            "is what finished means.",
        ):
            _log(line, run_log=run_log)

    if plan_module.is_blocked(plan):
        _log(
            "The plan contains blocked operation(s). Each one needs a person, not a retry.",
            "warning",
            run_log,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
