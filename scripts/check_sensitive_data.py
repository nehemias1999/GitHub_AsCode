"""Fail when the working tree contains data that must not be published.

The port of `scripts/Test-NoSensitiveData.ps1`. A repository that automates a live
platform accumulates sensitive material by accident: a token pasted into a comment, a
host name copied from an inventory, an absolute path from someone's workstation.
Reviews miss those; a mechanical gate does not.

Two layers.

1. **Structural rules**, built in and always on. They match the *shape* of sensitive
   data - credential formats, private address ranges, internal DNS suffixes,
   workstation paths - so they keep working without anyone maintaining a list.
2. **An optional deny list** of literal terms, read from a file excluded from version
   control. Organization names, host names and project code names are themselves
   sensitive, so they must not be committed inside the very script that looks for them.

Every rule may carry allow expressions. A match satisfying one is an intentional
placeholder rather than a finding.

Three properties are load bearing, and each one is here because its absence was a real
defect in the inherited code:

**Binary is decided by content, not by extension.** The original read only an allowlist
of text extensions, which meant it never opened `.pem`, `.key`, `id_rsa`, `.netrc` or
`.sh` - the files most likely to hold a private key. It reported "no findings" and meant
"no findings in the files I chose to open".

**An unreadable file is a FINDING, not a skip.** A file locked by another process or
denied by an ACL used to be counted as clean, so the gate printed a total it had not
actually read. The one guard the repository's secret hygiene rests on failed in the
insecure direction, silently.

**Which layers ran is part of the result.** "No findings" on its own reads as a clean
bill of health, and the deny-list layer is the only one that can match an internal
identifier with no recognisable shape.

Unlike the PowerShell original this is importable, so the suite calls the functions
rather than extracting them from the file with a regular expression - which is what
`tests/automations/SensitiveDataGate.Tests.ps1` has to do, and why a closing brace out
of column 0 breaks it with "Could not extract".
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Directories that either are not ours to police or hold intentionally local data.
# __pycache__ is deliberately NOT here. git ignores it, so it is filtered out one step
# later and COUNTED as ignored - which keeps the reported number meaning "files git
# ignores" rather than "files I decided not to look at". Excluding it here made the two
# gates report 11 and 38 skipped for the same tree, and the difference was mine.
EXCLUDED_DIRECTORIES = frozenset({".git", ".local", "artifacts", "node_modules"})

# Extensions NOT worth reading, because they are binary. Everything else is read - a
# file with no extension included, since LICENSE and Dockerfile are the innocent cases
# but id_rsa, .netrc and .npmrc are the ones that matter.
BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz",
        ".zip", ".gz", ".tgz", ".7z", ".rar", ".nupkg",
        ".exe", ".dll", ".pdb", ".so", ".dylib", ".msi", ".cab",
        ".pdf", ".docx", ".xlsx", ".pptx",
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".mp3", ".mp4", ".avi", ".mov", ".wav",
    }
)

# Placeholder identities this repository uses on purpose. Keep the list short: every
# entry is a hole in the gate.
_ALLOWED_DOMAINS = (
    r"contoso\.com|contoso\.local|example\.com|example\.org|example\.net|"
    r"users\.noreply\.github\.com|json-schema\.org|learn\.microsoft\.com|"
    r"dev\.azure\.com|keepachangelog\.com|semver\.org"
)


@dataclass(frozen=True)
class Rule:
    """One structural pattern, and the placeholders it tolerates."""

    name: str
    description: str
    pattern: str
    allow: tuple[str, ...] = ()


RULES = (
    Rule(
        "AzureDevOpsPat",
        "String shaped like an Azure DevOps Personal Access Token.",
        r"(?<![A-Za-z0-9])[a-z2-7]{52}(?![A-Za-z0-9])",
    ),
    Rule(
        "LongOpaqueToken",
        "Long alphanumeric run with no word breaks: typical of a token or key.",
        r"(?<![A-Za-z0-9+/=])[A-Za-z0-9]{72,}(?![A-Za-z0-9+/=])",
        # A long hex run is normally a checksum or a fixture id.
        (r"^[0-9a-f]+$",),
    ),
    Rule("AtlassianApiToken", "Atlassian API token.", r"ATATT[A-Za-z0-9_\-]{20,}"),
    Rule(
        "GitHubToken",
        "GitHub classic personal access, OAuth, user-to-server, server-to-server or "
        "refresh token.",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
    ),
    Rule(
        # Separate from the rule above rather than folded into one alternation, because
        # the two have different shapes: the fine-grained value carries underscores in
        # its body, which [A-Za-z0-9]{30,} would stop at.
        #
        # It is also the one that matters most here: fine-grained is the token type this
        # repository recommends, so it is the credential a reader is most likely to be
        # holding - and the inherited rule set could not see it.
        "GitHubFineGrainedToken",
        "GitHub fine-grained personal access token.",
        r"github_pat_[A-Za-z0-9_]{20,}",
    ),
    Rule(
        "CloudAccessKey",
        "Cloud provider access key identifier.",
        r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])",
    ),
    Rule(
        "PrivateKeyBlock",
        "PEM or OpenSSH private key material.",
        r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----",
    ),
    Rule("SshPublicKey", "SSH public key body.", r"ssh-(?:rsa|dss|ed25519) AAAA[0-9A-Za-z+/]+"),
    Rule(
        "JsonWebToken",
        "Serialized JSON Web Token.",
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}",
    ),
    Rule(
        "AssignedSecret",
        "Credential-shaped key assigned a literal value.",
        r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\b"
        r"[\"']?\s*[:=]\s*[\"']?[^\s\"'<>{}$,;#)]{6,}",
        (
            r"(?i)[:=]\s*[\"']?(?:PENDING_OWNER_CONFIGURATION|<[^>]+>|null|true|false)",
            # Points at an environment variable NAME, not at a value.
            r"(?i)[:=]\s*[\"']?[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*[\"']?\s*$",
            # A PowerShell or macro expression.
            r"(?i)[:=]\s*[\"']?\$",
            # A command call, not a value.
            r"(?i)[:=]\s*(?:Get|New|Read|Resolve|Invoke|Test|Join|Split|Convert)[A-Za-z]*-",
        ),
    ),
    Rule(
        "PrivateIpAddress",
        "Address in a private or link-local range: identifies real infrastructure.",
        r"(?<![\d.])(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|169\.254)"
        r"\.\d{1,3}\.\d{1,3}(?![\d.])",
    ),
    Rule(
        "InternalDnsSuffix",
        "Host name in an internal DNS zone.",
        r"(?i)\b[a-z0-9][a-z0-9.-]{2,}\.(?:local|internal|intranet|corp|lan|loc|home|priv)\b",
        (r"(?i)(?:^|\.)contoso\.local$",),
    ),
    Rule(
        "WorkstationPath",
        "Absolute path containing a real user profile name.",
        r"(?i)[A-Za-z]:\\Users\\[A-Za-z0-9._-]+",
        (r"(?i)[A-Za-z]:\\Users\\(?:<[^>]+>|USERNAME|%USERNAME%)",),
    ),
    Rule(
        "UncSharePath",
        "UNC path pointing at a named file server.",
        r"\\\\[A-Za-z0-9][A-Za-z0-9._-]{2,}\\[A-Za-z0-9$._-]+",
    ),
    Rule(
        "EmailAddress",
        "Email address outside the approved placeholder domains.",
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        (rf"(?i)@(?:{_ALLOWED_DOMAINS})$",),
    ),
)


@dataclass(frozen=True)
class Finding:
    """One match, located and described. The matched text is truncated, never full."""

    rule: str
    file: str
    line: int
    match: str
    reason: str


@dataclass
class ScanResult:
    """What the scan found, and what it actually covered."""

    findings: list[Finding] = field(default_factory=list)
    scanned: int = 0
    skipped_binary: int = 0
    skipped_ignored: int = 0
    deny_term_count: int = 0

    @property
    def coverage(self) -> str:
        """The sentence that stops a structural-only pass reading as a full one."""
        if self.deny_term_count:
            return f"structural rules + {self.deny_term_count} deny term(s)"
        return "structural rules only, no deny terms loaded"


def is_allowed(value: str, allow: tuple[str, ...]) -> bool:
    """Whether a matched string is an approved placeholder."""
    return any(re.search(expression, value) for expression in allow)


def git_ignored_paths(root: Path, paths: list[Path]) -> set[Path]:
    """The subset of paths that git ignores.

    This gate exists to keep sensitive data out of a commit, so a file git ignores is
    out of scope by definition - and scanning it is worse than useless. A real
    installation has a filled-in .env, which is ignored, holds credentials on purpose,
    and would make the gate fail on every run. A gate that can never pass is a gate
    people learn to ignore, which is the exact failure this check exists to prevent.

    It asks git rather than keeping a second copy of the ignore rules, because a second
    copy drifts from .gitignore and nobody notices until it matters.

    One call: `git status --porcelain --ignored`, whose `!!` lines are the ignored
    entries. NOT `git check-ignore --stdin`, which looks like the right tool and is not:
    given paths on stdin it matched nothing and exited 1 - the same answer it gives for
    "not ignored" - while the identical paths as arguments matched correctly. Measured
    against git 2.46.0.windows.1. A silent wrong answer from a security check is worse
    than no check.

    When git is unavailable, or the directory is not a working copy, or the command
    fails, nothing is reported as ignored and the scan covers everything - failing loud
    rather than silently skipping.
    """
    if not paths or not (root / ".git").exists():
        return set()

    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--ignored"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if completed.returncode != 0:
        return set()

    entries = []
    for line in completed.stdout.splitlines():
        if not line.startswith("!! "):
            continue
        entry = line[3:].strip()
        if len(entry) >= 2 and entry.startswith('"') and entry.endswith('"'):
            entry = entry[1:-1]
        entries.append(entry)
    if not entries:
        return set()

    ignored = set()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        for entry in entries:
            # git reports an ignored directory as a single entry with a trailing slash,
            # so a directory match is a prefix match.
            if entry.endswith("/"):
                if relative.lower().startswith(entry.lower()):
                    ignored.add(path)
                    break
            elif relative.lower() == entry.lower():
                ignored.add(path)
                break
    return ignored


def scannable_files(root: Path) -> tuple[list[Path], int]:
    """Candidate files under a root, and how many git ignores.

    Returns the files to read and the count skipped, which the caller reports: a file
    skipped without saying so is how a scan quietly stops covering something.
    """
    candidates = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if EXCLUDED_DIRECTORIES.intersection(path.relative_to(root).parts[:-1]):
            continue
        if path.suffix.lower() in BINARY_EXTENSIONS:
            continue
        candidates.append(path)

    ignored = git_ignored_paths(root, candidates)
    return [path for path in candidates if path not in ignored], len(ignored)


def load_deny_terms(path: Path) -> list[str]:
    """Literal terms from the local deny list. A missing file yields none."""
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def scan_text(text: str, relative_path: str, deny_terms: list[str]) -> list[Finding]:
    """Apply every rule and every deny term to one file's text.

    Args:
        text: The file content.
        relative_path: How the file is named in a finding.
        deny_terms: Literal terms, matched case-insensitively.

    Returns:
        Every finding, in rule order then line order.
    """
    findings: list[Finding] = []

    # JSON escapes a backslash as two, so an ordinary path reads as a UNC share to a
    # pattern that knows nothing about the encoding. Unescaping before scanning removes
    # that false positive without creating a blind spot: a genuine UNC path is written
    # with four backslashes in JSON, which unescapes to two and is still matched. The
    # replacement is character for character, so reported line numbers stay correct.
    scan = text.replace("\\\\", "\\") if relative_path.endswith(".json") else text

    for rule in RULES:
        for match in re.finditer(rule.pattern, scan):
            if is_allowed(match.group(0), rule.allow):
                continue
            value = match.group(0)
            findings.append(
                Finding(
                    rule=rule.name,
                    file=relative_path,
                    line=scan.count("\n", 0, match.start()) + 1,
                    # Truncated, because a finding is printed and a full credential in
                    # the output of a credential scanner is the thing itself.
                    match=value[:12] + "...[redacted]" if len(value) > 40 else value,
                    reason=rule.description,
                )
            )

    for index, line in enumerate(scan.splitlines(), start=1):
        lowered = line.lower()
        for term in deny_terms:
            if term.lower() in lowered:
                findings.append(
                    Finding(
                        rule="DenyTerm",
                        file=relative_path,
                        line=index,
                        # Never the term itself: the deny list is sensitive, which is
                        # why it is not committed in the first place.
                        match="[deny term matched]",
                        reason="Literal term from the local deny list.",
                    )
                )

    return findings


def scan(root: Path, deny_terms: list[str] | None = None) -> ScanResult:
    """Scan a tree and report what was found and what was covered."""
    deny_terms = deny_terms or []
    result = ScanResult(deny_term_count=len(deny_terms))

    files, result.skipped_ignored = scannable_files(root)

    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
        except OSError as error:
            # Unreadable is a FINDING, not a skip. A file locked by another process or
            # denied by an ACL used to be counted as clean, so the gate printed a total
            # it had not read.
            result.findings.append(
                Finding(
                    rule="UnreadableFile",
                    file=relative,
                    line=0,
                    match="could not be read, so it was not scanned",
                    reason=f"The file could not be opened: {error.strerror or error}",
                )
            )
            continue

        # Binary despite the extension, or genuinely empty. Neither is a finding, but
        # neither is "scanned" either, so neither counts towards the total.
        if not raw:
            continue
        if b"\x00" in raw:
            result.skipped_binary += 1
            continue

        result.scanned += 1
        result.findings.extend(
            scan_text(raw.decode("utf-8", errors="replace"), relative, deny_terms)
        )

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on sensitive data in the working tree.")
    parser.add_argument("--path", default=None, help="Root to scan. Defaults to the repository.")
    parser.add_argument("--terms-file", default=".local/sensitive-terms.txt")
    parser.add_argument(
        "--require-terms-file",
        action="store_true",
        help=(
            "Treat an absent or empty deny list as a failure (exit 2) instead of running "
            "the structural rules alone. NOT the default on purpose: a check that cannot "
            "pass on a fresh clone is a check people learn to ignore."
        ),
    )
    arguments = parser.parse_args(argv)

    repository_root = Path(__file__).resolve().parents[1]
    root = Path(arguments.path).resolve() if arguments.path else repository_root

    terms_path = Path(arguments.terms_file)
    if not terms_path.is_absolute():
        terms_path = repository_root / terms_path
    deny_terms = load_deny_terms(terms_path)

    if not terms_path.is_file():
        print(
            f"WARNING: Deny-term file not found: {terms_path}. Structural rules only - "
            "this is not a full scan.",
            file=sys.stderr,
        )
    elif not deny_terms:
        # Present but with no usable line in it. The file existing is what silences the
        # warning above, so an empty one would otherwise buy false confidence.
        print(
            f"WARNING: Deny-term file {terms_path} has no terms in it. Structural rules "
            "only - this is not a full scan.",
            file=sys.stderr,
        )

    result = scan(root, deny_terms)

    if result.skipped_ignored:
        print(
            f"[secrets] {result.skipped_ignored} file(s) skipped because git ignores them "
            "(a filled-in .env is the usual one)."
        )

    if not result.findings:
        print(
            f"Sensitive data gate: no findings across {result.scanned} file(s) "
            f"({result.coverage})."
        )
        if arguments.require_terms_file and not deny_terms:
            print(
                "WARNING: The deny-list layer was required and did not run. Add terms to "
                f"{terms_path}, or drop --require-terms-file to accept structural coverage "
                "only.",
                file=sys.stderr,
            )
            return 2
        return 0

    print(
        f"WARNING: Sensitive data gate: {len(result.findings)} finding(s) across "
        f"{result.scanned} file(s) ({result.coverage}).",
        file=sys.stderr,
    )
    for finding in sorted(result.findings, key=lambda item: (item.file, item.line)):
        print(f"  {finding.rule:<24} {finding.file}:{finding.line}  {finding.match}")

    counts: dict[str, tuple[int, str]] = {}
    for finding in result.findings:
        count, _ = counts.get(finding.rule, (0, finding.reason))
        counts[finding.rule] = (count + 1, finding.reason)
    print()
    for rule, (count, reason) in sorted(counts.items(), key=lambda item: -item[1][0]):
        print(f"  {rule:<24} {count:>4}  {reason}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
