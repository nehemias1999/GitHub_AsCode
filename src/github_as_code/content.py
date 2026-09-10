"""Repository contents: which declared files a repository actually has.

The domain half of phase 2. Everything that decides something is a pure function over
values; the one function that reaches the network does nothing but fetch a directory
listing, so the rules stay testable offline.

**The whole design turns on one problem: a 404 does not mean the file is absent.**

GitHub answers 404 both for something that does not exist and for something the token
cannot see, and this repository's rule 7 says never to read the second as the first. For
contents that is not a hypothetical: a fine-grained token with `Metadata: read` and
without `Contents: read` lists every repository perfectly well and then 404s on every
path inside them. Reading those as "the file is missing" would produce a plan saying
twenty repositories lack a README, with nothing anywhere saying the token could not look.

So the root listing is fetched first, once per repository, and it is what establishes
whether contents are readable at all:

  root listing succeeds -> contents are readable, and a 404 below it IS absence
  root listing 404s     -> blocked; nothing about the files is reported

That is the "absence established another way" the transport's `allow_not_found` is
documented as requiring. It also makes the walk cheap: one call per repository plus one
per distinct subdirectory the standards name, rather than one per declared file.
"""

from dataclasses import dataclass, field

from github_as_code import plan as plan_module
from github_as_code import rest


class ContentError(Exception):
    """A declared standard cannot be checked."""


@dataclass(frozen=True)
class RequiredFile:
    """One file a class of repository must have, and why."""

    path: str
    reason: str = ""

    @property
    def directory(self) -> str:
        """The directory the file lives in, '' for the repository root."""
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Standard:
    """What a class of repository is required to contain."""

    name: str
    description: str
    required_files: tuple[RequiredFile, ...] = ()


@dataclass(frozen=True)
class ContentStatus:
    """The verdict for one repository, in the vocabulary the plan enforces."""

    action: str
    status: str
    reason: str
    missing: tuple[str, ...] = ()
    present: tuple[str, ...] = ()

    def __post_init__(self):
        if self.action not in plan_module.PLAN_ACTION:
            raise plan_module.PlanVocabularyError(f"Unknown plan action '{self.action}'.")
        if self.status not in plan_module.PLAN_STATUS:
            raise plan_module.PlanVocabularyError(f"Unknown plan status '{self.status}'.")


@dataclass
class RepositoryContents:
    """What one repository holds, as far as the token could see.

    `readable` is the field that matters. False means the root listing was refused, and
    every other field is meaningless rather than empty - which is why nothing reads them
    without checking it first.
    """

    readable: bool
    listings: dict[str, frozenset[str]] = field(default_factory=dict)
    detail: str = ""

    def has(self, path: str) -> bool:
        """Whether a path is present, given the directories that were listed."""
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        name = path.rsplit("/", 1)[-1]
        return name in self.listings.get(directory, frozenset())


def normalise_path(path: str) -> str:
    """Check a declared file path, and return it unchanged.

    A standards declaration names paths that become URL segments, and `build_uri`
    escapes the query rather than the path - the same reasoning that makes
    `format_repository_name` validate in code. A leading slash, a traversal segment or a
    backslash is refused here, offline, rather than becoming a request.

    Args:
        path: The declared path, relative to the repository root.

    Returns:
        The path, unchanged.

    Raises:
        ContentError: The path is empty, absolute, or contains a traversal segment.

    Example:
        >>> normalise_path(".github/workflows/ci.yml")
        '.github/workflows/ci.yml'
    """
    if not path or not path.strip():
        raise ContentError("A required file path cannot be empty.")
    if path != path.strip():
        raise ContentError(f"The path '{path}' has surrounding whitespace.")
    if path.startswith("/"):
        raise ContentError(
            f"The path '{path}' starts with '/'. Declare it relative to the repository "
            "root, because that is what the API addresses."
        )
    if "\\" in path:
        raise ContentError(
            f"The path '{path}' contains a backslash. The API addresses paths with "
            "forward slashes on every platform."
        )
    if any(segment in ("", ".", "..") for segment in path.split("/")):
        raise ContentError(
            f"The path '{path}' contains an empty or relative segment, which is path "
            "traversal once it becomes a URL segment."
        )
    return path


def required_directories(standards: dict[str, Standard]) -> list[str]:
    """Every distinct directory the standards name, root first.

    Fetching by directory rather than by file is what keeps the walk to one call per
    directory instead of one per declared file - and the root is always included,
    because it is what establishes that contents are readable at all.

    Example:
        >>> required_directories({"s": Standard("s", "", (RequiredFile("a/b.md"),))})
        ['', 'a']
    """
    directories = {""}
    for standard in standards.values():
        for required in standard.required_files:
            directories.add(required.directory)
    return [""] + sorted(directory for directory in directories if directory)


def fetch_contents(
    context: rest.GitHubContext,
    owner: str,
    repository: str,
    directories: list[str],
    transport=None,
) -> RepositoryContents:
    """Read the directory listings a standards check needs.

    The root is read first and decides everything else: if it is refused, the token
    cannot see this repository's contents and no statement about its files would be
    honest.

    A subdirectory answering 404 is genuinely absent - the root listing already
    established that contents are readable - so every declared file under it is missing.

    Args:
        context: The context from `rest.new_context`.
        owner: Account login.
        repository: Repository name.
        directories: Directories to list, as `required_directories` returns them.
        transport: Injected for tests.

    Returns:
        A RepositoryContents. Check `readable` before reading anything else.
    """
    call = {} if transport is None else {"transport": transport}
    listings: dict[str, frozenset[str]] = {}

    for directory in directories:
        path = f"repos/{owner}/{repository}/contents"
        if directory:
            path = f"{path}/{directory}"

        response = rest.request(context, path, allow_not_found=True, **call)

        if response is None:
            if directory == "":
                # The root. Absence here is not absence - it is the token, and saying
                # anything about the files would be a claim this run cannot support.
                return RepositoryContents(
                    readable=False,
                    detail=(
                        "The contents of this repository could not be read. On GitHub a 404 "
                        "means either 'no such repository' or 'this token cannot see it', "
                        "and the account listing already returned this one - so the likely "
                        "cause is a token without Contents: read."
                    ),
                )
            # A subdirectory. The root listing succeeded, so this really is absent.
            listings[directory] = frozenset()
            continue

        body = response.content
        if not isinstance(body, list):
            # A path that names a FILE answers with an object rather than an array.
            # Treating that as a listing would silently report every file under it as
            # missing.
            listings[directory] = frozenset()
            continue

        listings[directory] = frozenset(
            str(entry.get("name")) for entry in body if entry.get("name")
        )

    return RepositoryContents(readable=True, listings=listings)


def standards_status(standard: Standard, contents: RepositoryContents) -> ContentStatus:
    """Compare one repository's contents against the standard for its class.

    Four cases, and the first is the one a naive implementation gets wrong:

    Contents unreadable -> resolve / blocked. NOT "every file is missing". The run could
    not look, and a plan that reports twenty missing READMEs because the token lacked a
    permission is worse than one that fails.

    Nothing required -> validate / ok. A class may exist to say "no files are required",
    and that is a decision rather than an omission.

    Everything present -> exists / ok.

    Something missing -> add / pending. Phase 4 would create them; this reports.

    Args:
        standard: The standard for the repository's class.
        contents: What `fetch_contents` found.

    Returns:
        A ContentStatus.

    Example:
        >>> standards_status(Standard("s", ""), RepositoryContents(readable=False)).status
        'blocked'
    """
    if not contents.readable:
        return ContentStatus(
            action="resolve",
            status="blocked",
            reason=contents.detail or "The contents of this repository could not be read.",
        )

    if not standard.required_files:
        return ContentStatus(
            action="validate",
            status="ok",
            reason=(
                f"The '{standard.name}' class requires no files, so there is nothing to "
                "compare."
            ),
        )

    missing = tuple(
        required.path for required in standard.required_files if not contents.has(required.path)
    )
    present = tuple(
        required.path for required in standard.required_files if contents.has(required.path)
    )

    if not missing:
        return ContentStatus(
            action="exists",
            status="ok",
            reason=(
                f"All {len(present)} file(s) the '{standard.name}' class requires are "
                "present."
            ),
            present=present,
        )

    reasons = {
        required.path: required.reason
        for required in standard.required_files
        if required.reason
    }
    detail = ", ".join(
        f"{path} ({reasons[path]})" if path in reasons else path for path in missing
    )
    return ContentStatus(
        action="add",
        status="pending",
        reason=(
            f"Missing {len(missing)} of {len(standard.required_files)} file(s) the "
            f"'{standard.name}' class requires: {detail}. repo-standards apply would "
            "create these in phase 4; this run only reports them."
        ),
        missing=missing,
        present=present,
    )


def undeclared_class_status(class_name: str, known: list[str]) -> ContentStatus:
    """The verdict for a repository whose class no standard describes.

    Separate from `standards_status` because it is a different question, and because
    reporting it as "nothing missing" would be a lie by omission: the run did not check,
    it had nothing to check against.
    """
    return ContentStatus(
        action="resolve",
        status="blocked",
        reason=(
            f"The class '{class_name}' has no standard, so nothing states what this "
            f"repository should contain. Declared classes: {', '.join(known) or 'none'}."
        ),
    )
