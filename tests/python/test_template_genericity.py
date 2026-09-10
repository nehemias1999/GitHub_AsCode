"""The template stays generic.

This repository is published as a GitHub template, and it was not written that way: it
was written against one real account, and the first cleanup removed around 120
references to that account's login, its repository names, its measured baseline, and the
machine it was developed on.

A cleanup is a one-time event. These tests are what make genericity a property. Without
them the next commit written while looking at a real account reintroduces the problem,
and nobody notices until somebody else generates a repository and finds a stranger's
data in it.

**Ported deliberately, not incidentally.** These guards were in the PowerShell suite and
have nothing to do with PowerShell - they are about the repository being a coherent
template. Deleting that suite would have dropped them silently, which is the exact
failure the sensitive-data ordering constraint was written to prevent, applied to a set
of checks nobody had listed. They are listed now.
"""

import re
import subprocess
import unittest

from support import REPO_ROOT

# Two exemptions, each with its reason. Every future one needs the same standard of
# justification, written here, and a test below asserts the list has not grown past what
# is documented.
#
# LICENSE names a person on purpose. The copyright holder of an MIT licence is a legal
# fact about who wrote the code, and removing it would make the template worse rather
# than more generic.
#
# This file exempts itself, and that is not a convenience. A guard has to be able to NAME
# what it forbids - the comments below quote a build-level version number and the exact
# phrasing that misattributes a measurement - because a rule whose reason cannot be
# written down is a rule somebody deletes.
#
# This exact mistake was made twice before it was understood. The inherited "network I/O
# in exactly one place" guard matched raw file content, so it fired on the comment that
# EXPLAINS where the network call lives - satisfiable only by deleting the explanation.
# That one was fixed by reading the parse tree. Then the genericity suite was written,
# and on its first CI run it failed on its own three explanatory comments. The
# parse-tree fix does not transfer: most of what this guard scans is Markdown, which has
# no AST.
EXEMPT = ("LICENSE", "test_template_genericity.py")


def tracked_files() -> list:
    """Every file git tracks, which is what reaches somebody generating a repository.

    Deliberately NOT a filesystem walk: `artifacts/`, `.env` and the active declaration
    are excluded from version control and hold real data by design, so scanning them
    would report findings that are not findings.
    """
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return []
    paths = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        path = REPO_ROOT / line
        if path.is_file() and path.name not in EXEMPT:
            paths.append(path)
    return paths


def find_in_tracked(pattern: str) -> list[str]:
    """Every 'path:line' matching a pattern.

    Fails loudly on a file it cannot read, rather than skipping it. A genericity guard
    that silently passes over a file it could not open reports a clean result it did not
    establish - the same failure the sensitive data gate had, and this one is not going
    to copy it.
    """
    compiled = re.compile(pattern)
    hits = []
    for path in tracked_files():
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise AssertionError(
                f"Could not read tracked file '{path}': {error}. The genericity guard "
                "cannot report a clean result for a file it did not read."
            ) from error
        if b"\x00" in raw:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if compiled.search(line):
                hits.append(f"{relative}:{number}: {line.strip()}")
    return hits


class TheTemplateNamesNoParticularAccount(unittest.TestCase):
    def test_has_tracked_files_to_scan_at_all(self):
        # Guards the guard. If `git ls-files` returns nothing - wrong working directory,
        # git absent from PATH - every test below passes vacuously, which is the worst
        # possible outcome for a suite whose entire job is to find things.
        self.assertGreater(len(tracked_files()), 40)

    def test_points_no_github_url_at_a_real_account(self):
        # A link to somebody else's repository is worse than no link: it works, so
        # nobody questions it, and it sends the reader somewhere that is not their
        # project.
        allowed = "owner|OWNER|EXAMPLE|your-|octocat|<[^>]+>"
        found = find_in_tracked(
            rf"github\.com/(?!({allowed}))[A-Za-z0-9][A-Za-z0-9-]{{0,38}}/"
        )
        self.assertEqual([], found, "\n".join(found))

    def test_contains_no_workstation_path(self):
        # A user profile path in a committed file says who built this and on what. It
        # also breaks for every reader, since the path does not exist on their machine.
        found = find_in_tracked(r"[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9._-]+")
        found += find_in_tracked(r"/home/[a-z][a-z0-9._-]+/")
        self.assertEqual([], found, "\n".join(found))

    def test_states_no_engine_or_tool_version_as_a_local_measurement(self):
        # A four-part build number is a fact about one machine on one day. It reads as
        # current, expires on its own, and tells the reader nothing about their own
        # environment. Version FLOORS - 3.11 - are requirements and stay.
        found = find_in_tracked(r"\b\d+\.\d+\.\d{3,}(\.\d+)?\b")
        self.assertEqual([], found, "\n".join(found))

    def test_publishes_no_count_of_the_repositories_in_another_account(self):
        # A count is still data about somebody's account, and the number of private
        # repositories somebody owns is not the template's to publish. What replaced it
        # is the METHOD - problem-statement.md carries the commands that produce the
        # reader's own table - which is more useful anyway, because a baseline is only
        # worth having if it is yours.
        found = []
        for shape in (r"\|[^|]*\*\*[0-9]+\*\*[^|]*\|", r"\b[0-9]+ (public|private)\b"):
            found += [line for line in find_in_tracked(shape) if ".md:" in line]
        self.assertEqual([], found, "\n".join(found))

    def test_reports_a_measurement_as_belonging_to_one_account_not_to_the_reader(self):
        # The baseline numbers are kept on purpose - they are the evidence that the API
        # traps are real. What they must not do is address the reader as though the
        # numbers were theirs.
        found = find_in_tracked(r"(?i)\b(on|against) (this|the live) account\b")
        # A runtime log line is the exception and is correct: when a run prints "on this
        # account" it is talking about the account it just read.
        in_documentation = [line for line in found if not line.startswith("src/")]
        self.assertEqual([], in_documentation, "\n".join(in_documentation))


class TheTemplateAsksToBePersonalised(unittest.TestCase):
    def test_ships_the_author_placeholder_rather_than_a_name(self):
        # The inverse assertion, and the reason this file is not just a blocklist. If a
        # real name ever lands in the manifest it will be because somebody personalised
        # their generated repository - which is what should happen - or because the
        # template regressed.
        manifest = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("TEMPLATE-AUTHOR", manifest)

    def test_documents_how_to_replace_the_placeholder(self):
        # A placeholder with no instructions is a defect. This is the link between the
        # test above and the reader.
        guide = REPO_ROOT / "docs/guides/using-this-template.md"
        self.assertIn("TEMPLATE-AUTHOR", guide.read_text(encoding="utf-8"))

    def test_exempts_exactly_the_files_whose_exemption_is_written_down(self):
        # An allowlist nothing checks grows. Every entry carries a paragraph explaining
        # why, and this is what forces the next person to write theirs: a third exemption
        # fails here until this assertion is updated deliberately, in the same commit,
        # next to the reason.
        self.assertEqual(("LICENSE", "test_template_genericity.py"), EXEMPT)

    def test_keeps_the_licence_holder_out_of_the_placeholder_scheme(self):
        # LICENSE is exempt from the scans above, so assert what it must contain rather
        # than leaving the exemption unchecked. An MIT licence with a placeholder holder
        # is not a licence.
        licence = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertRegex(licence, r"(?i)MIT License")
        self.assertNotIn("TEMPLATE-AUTHOR", licence)


if __name__ == "__main__":
    unittest.main()
