"""Loading and validating declared state.

The port of GitHubAsCode.Configuration.psm1, minus the engine selection that ADR 0007
drops. Two jobs, both boring on purpose:

(The word in that sentence was originally the one this repository forbids everywhere,
and the absence test caught it in a docstring. AGENTS.md section 4 says to rename the
code rather than loosen the guard, and a guard that could tell prose from a call would
be a guard with an exemption.)

  - Read environment variables from a .env file into the process, so a value lives on a
    workstation or in a pipeline secret rather than in Git.
  - Read a JSON configuration file and validate it against the schema it points at,
    before any network call happens.

The second job closes a gap worth naming. Shipping a JSON Schema next to a
configuration file and never running it is common and worthless: the schema documents
an intention while the loader accepts anything. Here `validate` actually validates, and
it does so offline, so a malformed catalogue fails in a second instead of halfway
through an apply.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from github_as_code import schema as schema_module

# Environment variables a .env file may never set. Each one changes where an
# interpreter finds code or executables, so allowing a configuration file to set it
# turns .env into a code execution path.
#
# The list is the union of two loaders' hazards, not a translation of one. The
# PowerShell names stay because both implementations read the same .env file during the
# transition, and a name that is harmless to Python is not harmless to the PowerShell
# run reading the line beside it. The Python names are new and are the ones that matter
# here: PYTHONPATH and PYTHONHOME move where `import` resolves, and PYTHONSTARTUP names
# a file that is executed.
#
# Compared case-insensitively, because the Windows environment is case-insensitive and
# a rule that PATH cannot be set must not be satisfied by writing Path.
PROTECTED_ENVIRONMENT_NAMES = frozenset(
    name.lower()
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONEXECUTABLE",
        "PYTHONUSERBASE",
        "PYTHONWARNINGS",
        "PSModulePath",
        "PSExecutionPolicyPreference",
        "PSHOME",
        "Path",
        "PATHEXT",
        "ComSpec",
        "DOTNET_STARTUP_HOOKS",
        "DOTNET_ADDITIONAL_DEPS",
        "COREHOST_TRACE",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
    )
)

_VALID_NAME_CHARACTERS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


class ConfigurationError(Exception):
    """The declared state, or the environment it needs, is wrong."""


@dataclass(frozen=True)
class Declaration:
    """Which file a run will read, and whether it fell back to the template."""

    path: Path
    used_template: bool
    active_path: Path | None


def _is_valid_variable_name(name: str) -> bool:
    if not name or name[0].isdigit():
        return False
    return all(character in _VALID_NAME_CHARACTERS for character in name)


def load_environment(paths: str | list[str], optional: bool = False) -> list[str]:
    """Load KEY=VALUE pairs from one or more .env files into the process environment.

    A single value may contain comma-separated paths, because the pipeline definitions
    pass a list as one parameter value. A missing file is an error rather than a silent
    skip: a run that quietly proceeds without its credentials fails later with a
    confusing message.

    Blank lines and lines starting with '#' are ignored. Surrounding single or double
    quotes are stripped, so a value with trailing spaces can be expressed.

    A name must look like an environment variable, and a small set of names that steer
    an interpreter is refused outright. A .env file is operator-edited, unsigned and
    unhashed, and this function writes what it names into the process environment, so
    without that constraint the file is a code execution path rather than a
    configuration one. Both refusals raise rather than skip, because a silently ignored
    line in a credential file is how a run proceeds without the credential it needed.

    Args:
        paths: One or more file paths; a single value may be comma-separated.
        optional: Skip a path that does not exist instead of failing.

    Returns:
        The names of the variables that were set. Names only, never values.

    Raises:
        ConfigurationError: A required file is missing, a name is malformed, or a name
            is one that steers the interpreter.

    Example:
        >>> load_environment(".env", optional=True)
        []
    """
    if isinstance(paths, str):
        paths = [paths]
    resolved = [part.strip() for value in paths for part in value.split(",") if part.strip()]

    names: list[str] = []
    for candidate in resolved:
        file = Path(candidate)
        if not file.is_file():
            if optional:
                continue
            raise ConfigurationError(
                f"Environment file not found: {file}. Run the bootstrap to create it "
                "from .env.example."
            )

        for line in file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue

            name, _, value = stripped.partition("=")
            name = name.strip()
            value = value.strip()

            if not _is_valid_variable_name(name):
                raise ConfigurationError(
                    f"Invalid variable name '{name}' in '{file}'. A name must start with a "
                    "letter or underscore and contain only letters, digits and underscores."
                )
            if name.lower() in PROTECTED_ENVIRONMENT_NAMES:
                raise ConfigurationError(
                    f"Refusing to set '{name}' from '{file}'. That variable controls how code "
                    "or executables are resolved, so a configuration file is not allowed to "
                    "change it."
                )

            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]

            os.environ[name] = value
            names.append(name)

    return names


def resolve_path(path: str | Path, root_path: str | Path) -> Path:
    """Turn a repository-relative path into an absolute one.

    Every entry point accepts path overrides, and a relative path has to mean the same
    thing whether the command was launched from the repository root, a module folder,
    or a build agent working directory.

    Args:
        path: Absolute or relative path.
        root_path: Base for relative paths, normally the repository root.

    Returns:
        An absolute path.

    Example:
        >>> resolve_path("/tmp", "/anywhere").is_absolute()
        True
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (Path(root_path) / candidate).resolve()


def resolve_declaration(
    project_context: dict,
    module: str,
    repository_root: str | Path,
    configuration_path: str | None = None,
) -> Declaration:
    """Decide which declaration file a run will read.

    The rule is the same for every automation: an explicit path wins; otherwise the
    active declaration if it exists; otherwise the versioned template. It was written
    out three times in the three entry points, and a rule implemented three times is a
    rule that drifts.

    The fallback to the template is what keeps `validate` runnable in a fresh clone,
    where the active file does not exist yet. It is returned rather than logged here,
    because this module knows nothing about how a caller reports - but a caller must
    report it, so a run never silently checks the template while the operator believes
    it checked their own declaration.

    Args:
        project_context: Parsed project context, whose automations section names both
            paths.
        module: Automation name, used to look itself up in that section.
        repository_root: Root that relative paths resolve against.
        configuration_path: Explicit override. When given, nothing else is consulted.

    Returns:
        A Declaration.

    Raises:
        ConfigurationError: The project context does not describe this automation.

    Example:
        >>> declared = {"configuration": "a.json", "template": "b.json"}
        >>> resolve_declaration({"automations": {"x": declared}}, "x", ".").used_template
        True
    """
    if configuration_path:
        return Declaration(
            path=resolve_path(configuration_path, repository_root),
            used_template=False,
            active_path=None,
        )

    automations = project_context.get("automations", {})
    if module not in automations:
        raise ConfigurationError(
            f"The project context does not describe an automation named '{module}'."
        )

    module_context = automations[module]
    active = resolve_path(module_context["configuration"], repository_root)
    if active.is_file():
        return Declaration(path=active, used_template=False, active_path=active)

    return Declaration(
        path=resolve_path(module_context["template"], repository_root),
        used_template=True,
        active_path=active,
    )


def duplicate_values(values: list[Any]) -> list[Any]:
    """The values that appear more than once, each reported once.

    Two entries for one resource would each report their own verdict about it, which is
    why every automation checks - and why the check belongs in one place rather than in
    six.

    Args:
        values: Values to inspect. An empty input yields nothing rather than failing.

    Returns:
        Each duplicated value, once, in the order first seen.

    Example:
        >>> duplicate_values(["a", "b", "a"])
        ['a']
    """
    seen: set[Any] = set()
    duplicated: list[Any] = []
    for value in values:
        if value in seen and value not in duplicated:
            duplicated.append(value)
        seen.add(value)
    return duplicated


def load_configuration(
    path: str | Path,
    schema_path: str | Path | None = None,
    skip_validation: bool = False,
) -> Any:
    """Read a JSON configuration file and validate it against its schema.

    The configuration files declare their own schema through a relative `$schema`
    property, which keeps the pairing next to the data instead of in a lookup table
    that drifts. This function resolves that relative reference against the file's own
    folder, validates, and raises with every error listed - not just the first, since a
    half-corrected file costs another round trip.

    Args:
        path: Path to the configuration file.
        schema_path: Explicit schema path, overriding the `$schema` property.
        skip_validation: Return the parsed document without validating. Intended for a
            test fixture reader, not for production paths.

    Returns:
        The parsed configuration document.

    Raises:
        ConfigurationError: The file is missing, is not JSON, declares no schema, or
            does not satisfy the schema it declares.

    Example:
        >>> load_configuration("nowhere.json")  # doctest: +SKIP
    """
    file = Path(path)
    if not file.is_file():
        raise ConfigurationError(
            f"Configuration file not found: {file}. If the repository ships a template, "
            "copy it by renaming the .example file."
        )

    raw = file.read_text(encoding="utf-8")
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise ConfigurationError(
            f"Configuration file '{file}' is not valid JSON: {error}"
        ) from error

    if skip_validation:
        return document

    if schema_path is None:
        if not isinstance(document, dict) or "$schema" not in document:
            raise ConfigurationError(
                f"Configuration file '{file}' does not declare a $schema property, and no "
                "schema path was supplied. Every configuration file must point at the schema "
                "that governs it."
            )
        schema_path = resolve_path(str(document["$schema"]), file.resolve().parent)

    result = schema_module.validate_document(raw, schema_path)
    if not result.is_valid:
        detail = "\n".join(f"  - {error}" for error in result.errors)
        raise ConfigurationError(
            f"Configuration file '{file}' does not satisfy its schema "
            f"({result.engine} validation):\n{detail}"
        )

    return document


def required_value(name: str) -> str:
    """Read a process environment variable named by the configuration.

    The configuration declares the NAME of every value the automations need; this turns
    a name into the value. It lives in the cross-cutting layer rather than in a
    transport module on purpose: there are two transports here, REST and GraphQL, and a
    rule implemented twice is a rule that drifts.

    It knows nothing about what the value is for, so it can never leak one into a
    message: the failure names the variable, never its content.

    Args:
        name: Name of the environment variable to read.

    Returns:
        The value, with surrounding whitespace removed - because these are typed by
        hand into a local file, and a trailing space on a base URL or a token produces
        a 401 that reads like bad credentials rather than like a typo.

    Raises:
        ConfigurationError: The variable is unset or empty.

    Example:
        >>> required_value("GITHUB_TOKEN_READ")  # doctest: +SKIP
    """
    value = os.environ.get(name, "")
    if not value.strip():
        raise ConfigurationError(
            f"Required environment variable '{name}' is not set. Add it to .env (see "
            ".env.example), or export it before running."
        )
    return value.strip()
