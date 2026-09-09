"""The gate the repository's secret hygiene rests on.

Ported from tests/automations/SensitiveDataGate.Tests.ps1, with one structural
improvement: those tests have to extract a function out of the script with the regular
expression `(?ms)^function Get-GitIgnoredPath \\{.*?^\\}`, so a closing brace that stops
being in column 0 breaks them with "Could not extract" - a failure that names the wrong
thing and sends the reader to the wrong file. Here the module is imported.

The cases that matter are the ones where the gate could pass while covering nothing:
a file it declined to open, a file it could not read, a layer that did not run.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from support import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_sensitive_data as gate  # noqa: E402

# Every trigger value is assembled rather than written, so this file does not trip
# the gate it is testing. The same idiom the Pester suite uses for token prefixes -
# and the first version of this file, which spelled them out, was caught by the gate
# on its own source. That is the gate working: it cannot tell a fixture from the real
# thing, and it should not try.
CLASSIC = ("gh" + "p_") + ("EXAMPLE" * 5)
LONG_CLASSIC = ("gh" + "p_") + ("EXAMPLE" * 8)
FINE_GRAINED = ("github" + "_pat_") + ("EXAMPLE" * 6)
PRIVATE_KEY = "-----BEGIN" + " RSA PRIVATE " + "KEY-----"
PRIVATE_IP = "10." + "14.2.7"
WORKSTATION_PATH = "C:" + chr(92) + "Users" + chr(92) + "someone"
REAL_EMAIL = "someone@" + "realcompany.io"
DENY_TERM = "CONTOSO-" + "PROJECT-ALPHA"


class TheGateFindsWhatItIsFor(unittest.TestCase):
    def scan_one(self, name: str, content: str) -> list[gate.Finding]:
        return gate.scan_text(content, name, [])

    def test_finds_a_private_key_block_in_a_file_with_no_extension(self):
        # id_rsa has no extension, and the inherited gate read only an allowlist of text
        # extensions - so the rule for private keys could never fire against the file
        # most likely to contain one.
        findings = self.scan_one("id_rsa", f"{PRIVATE_KEY}\nMIIEpAIBAAKCAQEA...\n")
        self.assertEqual(["PrivateKeyBlock"], [f.rule for f in findings])

    def test_finds_a_classic_token_in_a_shell_script(self):
        findings = self.scan_one("deploy.sh", f"export GITHUB_TOKEN={CLASSIC}\n")
        self.assertIn("GitHubToken", [f.rule for f in findings])

    def test_finds_a_fine_grained_token_which_is_the_type_this_repository_recommends(self):
        # The one credential a reader of this repository is most likely to be holding,
        # and the one the inherited rule set could not see.
        findings = self.scan_one(".netrc", f"password {FINE_GRAINED}\n")
        self.assertIn("GitHubFineGrainedToken", [f.rule for f in findings])

    def test_finds_a_private_address_that_identifies_real_infrastructure(self):
        findings = self.scan_one("hosts.conf", f"backend {PRIVATE_IP}\n")
        self.assertEqual(["PrivateIpAddress"], [f.rule for f in findings])

    def test_finds_a_workstation_path_but_not_a_placeholder_one(self):
        self.assertEqual(
            ["WorkstationPath"], [f.rule for f in self.scan_one("a.md", WORKSTATION_PATH)]
        )
        placeholder = "C:" + chr(92) + "Users" + chr(92) + "<USERNAME>"
        self.assertEqual([], self.scan_one("a.md", placeholder))

    def test_finds_an_email_outside_the_placeholder_domains(self):
        self.assertEqual([], self.scan_one("a.md", "someone@example.com"))
        self.assertEqual(["EmailAddress"], [f.rule for f in self.scan_one("a.md", REAL_EMAIL)])

    def test_reports_the_line_the_match_is_on(self):
        findings = self.scan_one("a.sh", f"first\nsecond\nexport T={CLASSIC}\n")
        self.assertEqual(3, findings[0].line)

    def test_never_prints_the_whole_of_a_long_match(self):
        # A finding is printed, and a full credential in the output of a credential
        # scanner is the thing itself. The threshold is 40 characters, so the value
        # here is deliberately longer than the one used elsewhere in this file.
        findings = self.scan_one("a.sh", f"export T={LONG_CLASSIC}\n")
        self.assertNotIn(LONG_CLASSIC, findings[0].match)
        self.assertIn("[redacted]", findings[0].match)

    def test_never_prints_the_deny_term_it_matched(self):
        # The deny list is sensitive, which is why it is not committed in the first
        # place. Echoing a term into a log would undo that.
        findings = gate.scan_text(f"the {DENY_TERM} name\n", "a.md", [DENY_TERM])
        self.assertEqual(["DenyTerm"], [f.rule for f in findings])
        self.assertNotIn(DENY_TERM, findings[0].match)

    def test_matches_a_deny_term_whatever_its_casing(self):
        findings = gate.scan_text(f"the {DENY_TERM.lower()} name\n", "a.md", [DENY_TERM])
        self.assertEqual(1, len(findings))


class TheGateDoesNotFireOnWhatItShouldTolerate(unittest.TestCase):
    def test_ignores_a_json_escaped_backslash_that_is_not_a_unc_path(self):
        # JSON escapes a backslash as two, so an ordinary path reads as a UNC share to a
        # pattern that knows nothing about the encoding. A genuine UNC path is written
        # with four, unescapes to two, and is still matched.
        self.assertEqual([], gate.scan_text(r'{"path": "Platform\\APP_ALPHA"}', "a.json", []))
        self.assertEqual(
            ["UncSharePath"],
            [f.rule for f in gate.scan_text(r'{"path": "\\\\server\\share"}', "a.json", [])],
        )

    def test_ignores_a_credential_key_pointing_at_an_environment_variable_name(self):
        key = "api" + "Key"
        self.assertEqual([], gate.scan_text(f"{key}: GITHUB_TOKEN_READ\n", "a.yml", []))

    def test_ignores_a_long_hex_run_which_is_a_checksum(self):
        self.assertEqual([], gate.scan_text("sha256:" + "a1b2c3d4" * 12 + "\n", "a.md", []))

    def test_passes_on_the_working_tree_as_it_stands(self):
        # The assertion that would fail first if any of the above were too eager.
        result = gate.scan(REPO_ROOT, [])
        located = [f"{f.rule} {f.file}:{f.line}" for f in result.findings]
        self.assertEqual([], result.findings, located)
        self.assertGreater(result.scanned, 50, "The scan read implausibly few files.")


class TheGateSaysWhatItActuallyCovered(unittest.TestCase):
    def test_names_the_layers_so_a_structural_only_pass_is_not_read_as_a_full_scan(self):
        # "No findings" on its own reads as a clean bill of health, and the deny-list
        # layer is the only one that can match an internal identifier with no shape.
        self.assertIn("no deny terms loaded", gate.ScanResult().coverage)
        self.assertIn("2 deny term(s)", gate.ScanResult(deny_term_count=2).coverage)

    def test_an_unreadable_file_is_a_finding_and_not_a_skip(self):
        # A file locked by another process or denied by an ACL used to be counted as
        # clean, so the gate printed a total it had not read. The one guard the whole
        # secret-hygiene claim rests on failed in the insecure direction, silently.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "locked.txt"
            target.write_text("nothing interesting\n", encoding="utf-8")

            original = gate.Path.read_bytes

            def refuse(self):
                if self.name == "locked.txt":
                    raise PermissionError(13, "Access is denied")
                return original(self)

            gate.Path.read_bytes = refuse
            try:
                result = gate.scan(root, [])
            finally:
                gate.Path.read_bytes = original

        self.assertEqual(["UnreadableFile"], [f.rule for f in result.findings])
        self.assertEqual(0, result.scanned, "An unreadable file must not count as scanned.")

    def test_does_not_read_a_binary_file_whatever_the_rules_would_have_matched(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            # A private key block behind a NUL byte: text rules would match it, and the
            # file is not text.
            (root / "blob.dat").write_bytes(b"\x00\x01" + PRIVATE_KEY.encode() + b"\x00")
            result = gate.scan(root, [])

        self.assertEqual([], result.findings)
        self.assertEqual(1, result.skipped_binary)
        self.assertEqual(0, result.scanned)

    def test_an_empty_file_is_neither_a_finding_nor_a_scanned_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty.txt").write_bytes(b"")
            result = gate.scan(root, [])
        self.assertEqual([], result.findings)
        self.assertEqual(0, result.scanned)

    def test_reads_a_file_with_no_extension_rather_than_declining_to_open_it(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "id_rsa").write_text(f"{PRIVATE_KEY}\n", encoding="utf-8")
            result = gate.scan(root, [])
        self.assertEqual(["PrivateKeyBlock"], [f.rule for f in result.findings])


class TheDenyListLayerIsRequiredOnlyWhenAsked(unittest.TestCase):
    def test_exits_two_when_the_layer_is_required_and_did_not_run(self):
        # A different code from "findings", because it means something different: the
        # scan was narrower than the caller demanded, not that anything was found.
        code = gate.main(["--terms-file", "does-not-exist.txt", "--require-terms-file"])
        self.assertEqual(2, code)

    def test_exits_zero_when_the_layer_is_absent_and_not_required(self):
        # NOT the default on purpose: a check that cannot pass on a fresh clone is a
        # check people learn to ignore, and this repository has been bitten by that.
        self.assertEqual(0, gate.main(["--terms-file", "does-not-exist.txt"]))

    def test_reads_terms_ignoring_blanks_and_comments(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "terms.txt"
            path.write_text("# a comment\n\nALPHA\n  BETA  \n", encoding="utf-8")
            self.assertEqual(["ALPHA", "BETA"], gate.load_deny_terms(path))

    def test_a_missing_terms_file_yields_no_terms_rather_than_failing(self):
        self.assertEqual([], gate.load_deny_terms(Path("nowhere.txt")))


class WhatGitIgnoresIsOutOfScope(unittest.TestCase):
    """A real installation has a filled-in .env, which is ignored and holds credentials
    on purpose. Scanning it would make the gate fail on every run, and a gate that can
    never pass is a gate people learn to ignore.
    """

    def test_reports_an_ignored_file_as_ignored(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
            (root / ".gitignore").write_text(".env\nartifacts/\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=x\n", encoding="utf-8")
            (root / "tracked.txt").write_text("nothing\n", encoding="utf-8")

            ignored = gate.git_ignored_paths(root, [root / ".env", root / "tracked.txt"])

        self.assertEqual({root / ".env"}, ignored)

    def test_reports_a_file_inside_an_ignored_directory(self):
        # git reports an ignored directory as a single entry with a trailing slash, so a
        # directory match has to be a prefix match.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
            (root / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
            (root / "artifacts").mkdir()
            (root / "artifacts" / "report.json").write_text("{}", encoding="utf-8")

            ignored = gate.git_ignored_paths(root, [root / "artifacts" / "report.json"])

        self.assertEqual({root / "artifacts" / "report.json"}, ignored)

    def test_covers_everything_when_the_directory_is_not_a_working_copy(self):
        # Failing loud rather than silently skipping: with no git to ask, nothing is
        # reported as ignored and the scan covers the lot.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("x", encoding="utf-8")
            self.assertEqual(set(), gate.git_ignored_paths(root, [root / "a.txt"]))

    def test_returns_nothing_for_an_empty_input_rather_than_failing(self):
        self.assertEqual(set(), gate.git_ignored_paths(REPO_ROOT, []))

    def test_the_repository_scan_skips_the_files_git_ignores(self):
        _, skipped = gate.scannable_files(REPO_ROOT)
        self.assertGreater(
            skipped, 0, "Nothing was reported as ignored, which is implausible here."
        )


class TheGateIsWiredIntoTheRunner(unittest.TestCase):
    def test_the_runner_no_longer_says_the_scan_is_missing(self):
        # That sentence was true until this port, and it is the condition
        # docs/process/port-status.md sets before the PowerShell gate can be removed.
        # If it comes back, the removal has quietly dropped a check.
        runner = (REPO_ROOT / "scripts" / "run_tests.py").read_text(encoding="utf-8")
        self.assertNotIn("is NOT part of this gate", runner)
        self.assertIn("check_secrets", runner)


if __name__ == "__main__":
    os.environ.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
    unittest.main()
