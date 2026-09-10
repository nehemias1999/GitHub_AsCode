"""Report which declared files each repository is missing, by class.

Command ladder. Nothing here writes to GitHub:

  validate   Offline. The declaration against its schema, plus the invariants a schema
             cannot express. No network, no token.
  inventory  Reads the contents of every declared repository and records what it found,
             comparing against nothing. The honest starting point.
  plan       Compares what each repository contains against the standard for its class.
  smoke      Plan plus the manual verification checklist.

There is no apply. Phase 4 would add one, behind its own confirmation, and it would
create files and never remove or overwrite them - see
docs/overview/scope-and-limits.md.

**Where the class of a repository comes from, and why not from here.** The declaration
this automation reads names classes and the files each requires. It does NOT name which
repository is in which class: that is declared once, in the repo-inventory declaration,
and read from there. Two files that must agree about which class a repository is in are
two files that drift, and the drift is silent - a repository would be checked against
the wrong standard and the plan would look perfectly reasonable. The project context
names both declarations, so the coupling is declared rather than assumed.

**The 404 problem, which is the whole difficulty of this phase.** GitHub answers 404
both for a file that does not exist and for one the token cannot see, so "the README is
missing" and "this token has no Contents: read" arrive identically. `content.py` settles
it by reading the repository root first: if that is refused the repository is `blocked`
and nothing is said about its files, and only once it succeeds is a 404 below it read as
absence.
"""

import argparse
import sys
from pathlib import Path

from github_as_code import configuration, content, http, report, repository, rest
from github_as_code import plan as plan_module

MODULE = "repo-standards"
INVENTORY_MODULE = "repo-inventory"


def _log(message: str, level: str = "info", run_log: Path | None = None) -> None:
    """One progress line, masked, and the same line in the transcript."""
    masked = report.protect_secrets_in_text(message)
    stream = sys.stderr if level == "warning" else sys.stdout
    prefix = "WARNING: " if level == "warning" else ""
    print(f"{prefix}[{MODULE}] {masked}", file=stream)
    report.add_run_log_line(run_log, level, message)


def read_standards(declaration: dict) -> dict[str, content.Standard]:
    """Turn the declaration into the standards the domain layer compares against."""
    standards = {}
    for name, entry in declaration["classes"].items():
        standards[name] = content.Standard(
            name=name,
            description=entry["description"],
            required_files=tuple(
                content.RequiredFile(path=item["path"], reason=item.get("reason", ""))
                for item in entry["requiredFiles"]
            ),
        )
    return standards


def _declaration_problems(
    declaration: dict, class_by_repository: dict[str, str], requested: list[str]
) -> list[str]:
    """The invariants a JSON Schema cannot express.

    Checked offline, where a mistake costs a second instead of failing partway through a
    run against a live account.
    """
    problems: list[str] = []
    declared_classes = set(declaration["classes"])

    for name, entry in declaration["classes"].items():
        seen: set[str] = set()
        for item in entry["requiredFiles"]:
            path = item["path"]
            # The same function the request path is built from, so a path that cannot be
            # addressed fails here rather than as a request nobody meant to send.
            try:
                content.normalise_path(path)
            except content.ContentError as error:
                problems.append(f"Class '{name}': {error}")
            if path in seen:
                problems.append(
                    f"Class '{name}' requires '{path}' twice. Two entries for one file "
                    "would each report their own verdict about it."
                )
            seen.add(path)

    # A class used by the inventory declaration and not described here means every
    # repository in it is checked against nothing. Reported offline rather than as a
    # blocked operation per repository, because it is one mistake and not twenty.
    used = set(class_by_repository.values())
    undescribed = sorted(used - declared_classes)
    if undescribed:
        problems.append(
            f"The repo-inventory declaration uses class(es) no standard describes: "
            f"{', '.join(undescribed)}. Declared here: {', '.join(sorted(declared_classes))}."
        )

    # The other direction is NOT an error. A standard for a class nothing uses yet is a
    # decision written down early, which this repository does elsewhere too.
    for name in requested:
        if name not in class_by_repository:
            problems.append(
                f"--repository-name '{name}' is not declared in the repo-inventory "
                "declaration, so this run has no class for it."
            )

    return problems


def _build_detail(
    *,
    command: str,
    declaration_path: Path,
    declaration_text: str,
    used_template: bool,
    requested: list[str],
    repository_root: Path,
    tool_version: str,
    run_log: Path | None,
    context: rest.GitHubContext,
    standards: dict[str, content.Standard],
    findings: dict[str, dict],
) -> dict:
    """The evidence block, computed once so the report and the summary agree."""
    unreadable = [name for name, item in findings.items() if not item["readable"]]
    missing_by_path: dict[str, int] = {}
    for item in findings.values():
        for path in item["missing"]:
            missing_by_path[path] = missing_by_path.get(path, 0) + 1

    return {
        "provenance": report.provenance(
            command=command,
            declaration_path=str(declaration_path),
            declaration_text=declaration_text,
            scope="repositoryName=" + ",".join(requested) if requested else "all",
            repository_root=repository_root,
            tool_version=tool_version,
            used_template=used_template,
        ),
        "runLog": str(run_log) if run_log else "",
        "apiBaseUrl": context.base_url,
        "owner": context.owner,
        "declarationPath": str(declaration_path),
        "classMappingFrom": INVENTORY_MODULE,
        "standard": {
            name: {
                "description": standard.description,
                "requiredFiles": [item.path for item in standard.required_files],
            }
            for name, standard in standards.items()
        },
        # The account-level summary. Which file is missing most often is the number that
        # decides what to do first, and it is not derivable from the operations without
        # counting them by hand.
        "finding": {
            "examined": len(findings),
            "unreadableCount": len(unreadable),
            "unreadable": sorted(unreadable),
            "missingByPath": dict(sorted(missing_by_path.items())),
        },
        "repository": {
            name: {
                "class": item["class"],
                "readable": item["readable"],
                "missing": list(item["missing"]),
                "present": list(item["present"]),
            }
            for name in sorted(findings)
            for item in [findings[name]]
        },
    }


def main(argv: list[str] | None = None, repository_root: Path | None = None) -> int:
    """Run one rung of the ladder.

    Returns:
        0 nothing blocked; 2 something could not be determined; 1 the run failed.
    """
    parser = argparse.ArgumentParser(prog=MODULE, description=__doc__)
    parser.add_argument("command", choices=("validate", "inventory", "plan", "smoke"))
    parser.add_argument("--repository-name", action="append", default=[])
    parser.add_argument("--env-file", action="append", default=None)
    parser.add_argument("--project-context-path", default=None)
    parser.add_argument("--configuration-path", default=None)
    parser.add_argument("--report-path", default=None)
    arguments = parser.parse_args(argv)

    root = Path(repository_root) if repository_root else Path(__file__).resolve().parents[3]
    run_log = report.start_run_log(root, MODULE, arguments.command)

    try:
        return _run(arguments, root, run_log)
    except (
        configuration.ConfigurationError,
        http.TransportError,
        repository.DeclarationError,
        content.ContentError,
    ) as error:
        _log(str(error), "warning", run_log)
        return 1


def _class_by_repository(project_context: dict, root: Path) -> tuple[dict[str, str], Path]:
    """Read the repository-to-class mapping from the repo-inventory declaration.

    Declared once, and read from where it is declared. The alternative - restating it
    here - is two files that must agree about which class a repository is in, and the
    disagreement would be silent: a repository checked against the wrong standard
    produces a plan that looks entirely reasonable.
    """
    choice = configuration.resolve_declaration(project_context, INVENTORY_MODULE, root)
    inventory = configuration.load_configuration(choice.path)
    mapping = {entry["name"]: entry["class"] for entry in inventory["repositories"]}
    return mapping, choice.path


def _run(arguments, root: Path, run_log: Path | None) -> int:
    command = arguments.command
    requested = list(arguments.repository_name)

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

    class_by_repository, mapping_path = _class_by_repository(project_context, root)
    _log(
        f"Repository classes read from the {INVENTORY_MODULE} declaration at "
        f"{mapping_path}: {len(class_by_repository)} repository/ies.",
        run_log=run_log,
    )

    problems = _declaration_problems(declaration, class_by_repository, requested)
    if problems:
        detail = "\n".join(f"  - {problem}" for problem in problems)
        raise configuration.ConfigurationError(
            f"The declaration satisfies its schema but is not executable:\n{detail}"
        )

    standards = read_standards(declaration)
    required_count = sum(len(standard.required_files) for standard in standards.values())
    _log(
        f"Schema and invariants: {len(standards)} class(es), {required_count} required "
        "file(s) in total. Valid (builtin validation).",
        run_log=run_log,
    )

    if command == "validate":
        _log("validate is offline and complete. Nothing was contacted.", run_log=run_log)
        return 0

    using_default = arguments.env_file is None
    env_files = [str(root / ".env")] if using_default else arguments.env_file
    configuration.load_environment(env_files, optional=using_default)

    context = rest.new_context(project_context)
    _log(
        f"Account: {context.owner} at {context.base_url}, token from "
        f"{context.token_environment_name}.",
        run_log=run_log,
    )

    directories = content.required_directories(standards)
    _log(
        f"Reading {len(directories)} director(ies) per repository: "
        f"{', '.join(name or '(root)' for name in directories)}.",
        run_log=run_log,
    )

    in_scope = sorted(requested) if requested else sorted(class_by_repository)
    plan = plan_module.new_plan(command, context.owner)
    findings: dict[str, dict] = {}

    for name in in_scope:
        class_name = class_by_repository[name]
        standard = standards.get(class_name)

        if standard is None:
            # Cannot happen after the invariant check above, and handled anyway: a
            # verdict that depends on an earlier check having run is a verdict that
            # breaks when somebody reorders the file.
            status = content.undeclared_class_status(class_name, sorted(standards))
            plan_module.add_operation(plan, "repository", name, status)
            findings[name] = {
                "class": class_name,
                "readable": True,
                "missing": (),
                "present": (),
            }
            continue

        contents = content.fetch_contents(context, context.owner, name, directories)

        if command == "inventory":
            # inventory records what is there and compares nothing, which is what makes
            # it runnable before a standard exists.
            if not contents.readable:
                status = content.standards_status(standard, contents)
            else:
                present = tuple(
                    item.path for item in standard.required_files if contents.has(item.path)
                )
                status = content.ContentStatus(
                    action="exists",
                    status="ok",
                    reason=(
                        f"Read {len(contents.listings)} director(ies). "
                        f"{len(present)} of {len(standard.required_files)} declared file(s) "
                        f"present."
                    ),
                    present=present,
                )
        else:
            status = content.standards_status(standard, contents)

        plan_module.add_operation(plan, "repository", name, status)
        findings[name] = {
            "class": class_name,
            "readable": contents.readable,
            "missing": status.missing,
            "present": status.present,
        }

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
            requested=requested,
            repository_root=root,
            tool_version=str(project_context.get("version", "")),
            run_log=run_log,
            context=context,
            standards=standards,
            findings=findings,
        ),
    )
    _log(f"Report: {written.json_path}", run_log=run_log)
    _log(f"Summary: {written.markdown_path}", run_log=run_log)

    if command == "smoke":
        _log("Manual verification checklist:", run_log=run_log)
        for line in (
            "  1. Open one repository the plan calls complete and confirm the files are "
            "really there. A standard that matches everything is usually a standard that "
            "checks nothing.",
            "  2. Confirm the unreadable count is zero. Anything above it means the token "
            "lacks Contents: read, and every file verdict below it is absent rather than "
            "wrong.",
            "  3. Re-run plan. It must report the same operations as the first run.",
            "  4. Read finding.missingByPath. The file missing most often is the one to "
            "decide about first, and it is usually a licence.",
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
