# 0006 - Python, and nothing but its standard library

**Status.** Accepted. Supersedes the *implementation language* of every earlier decision;
supersedes no decision itself.

## Context

This repository is pure PowerShell. That was never argued for on its merits - it was
inherited from `Jenkins_AsCode` and `ADO_AsCode`, which are PowerShell because the
platforms they audit are administered from Windows. Nothing in
`docs/overview/problem-statement.md` or `docs/overview/scope-and-limits.md` states a
Windows-only audience, and no document defends the choice on portability grounds.

The requirement that forced the question: **this has to run on Linux** - on CI agents and
in containers, and on a Linux workstation.

That requirement alone does not decide the language. PowerShell 7 is cross-platform, and
this codebase turns out to be close to portable already: paths are built with `Join-Path`
and `[System.IO.Path]`, `.gitattributes` pins `eol=lf` so a clone is byte-identical on
either platform, and nothing touches the registry, WMI or COM. Two things are genuinely
Windows-bound - `icacls` and `WindowsIdentity.GetCurrent()` in `scripts/bootstrap.ps1`,
and `USERNAME`/`COMPUTERNAME` in the provenance block - and both are a few lines.

So the honest framing is: making the PowerShell run on Linux is roughly half a day; this
is a rewrite of 7,911 lines. **The rewrite was chosen deliberately, with that comparison
on the table**, for reasons outside this document: the environments this must run in, and
who has to maintain it.

## Decision

**Python, floor 3.11, with no runtime dependency beyond the standard library and git.**

The second clause is the one that matters, and it is not new. It is the rule ADR 0004
already enforces, restated in the new language:

> this repository depends on nothing beyond PowerShell and git

becomes

> this repository depends on nothing beyond the Python standard library and git

`urllib.request` covers every network operation this tool performs. `json`, `hashlib`,
`hmac`, `re`, `argparse`, `email.utils` and `pathlib` cover the rest. A test runner and a
linter are development dependencies, exactly as Pester and PSScriptAnalyzer are today, and
the shipped tool imports neither.

**Floor 3.11**, chosen for one concrete reason rather than for novelty: `tomllib` is in the
standard library from 3.11, and the guard that enforces this very rule reads
`pyproject.toml` to assert `dependencies == []`. On 3.9 that guard would have to parse TOML
with a regex or take a dependency to check that there are no dependencies, and both are
worse than moving the floor. 3.11 also drops the `from __future__ import annotations`
ceremony and allows `match`.

The floor is enforced by a CI matrix, not statically. This is a real downgrade from
PowerShell: `PSUseCompatibleSyntax` checked the 5.1/7.0 floor on every machine, and Python
has no equivalent. `ruff`'s `target-version` catches a subset. Say so in
`docs/process/testing-strategy.md` rather than letting the loss go unrecorded.

## Consequences

**Two things get structurally better, and both were previously unenforceable.**

`docs/reference/architecture.md` says "dependencies point downward, never sideways".
PowerShell had no way to check that; Python does, because imports are statically
enumerable. That sentence stops being a convention and becomes a guard.

The same is true of the rule this ADR states. In PowerShell "no dependencies" held because
no package manager was in play. Python has pip, so the rule needs teeth: an AST check
against `sys.stdlib_module_names`, a manifest check on `pyproject.toml`, and an execution
check that runs `validate` under `python -I -S` with no `site-packages` at all. Three
layers because they fail differently - one catches an import, one catches a declared
dependency nobody imported yet, and one catches both by refusing to run.

**One class of bug disappears, along with its guard.** `ConvertTo-Json` defaults to
`-Depth 2` on Windows PowerShell 5.1 and silently truncates; `json.dumps` has no depth
limit. The absence test that enforced `-Depth` has nothing to test and is deleted rather
than ported. Its entry in `docs/reference/github-notes.md` is **rewritten, not removed** -
that document is the ledger of why each guard exists, and an entry deleted without trace
loses the reason.

**One class of bug arrives.** `urllib.request` follows redirects by default and re-sends
the headers set on the request - including `Authorization`. `GitHubAsCode.Http.psm1:730`
sets `MaximumRedirection = 0` precisely because forwarding a Bearer token to whatever a
30x names is credential disclosure. A naive port inverts the most carefully reasoned
security decision in the HTTP module, with no error and no log line. The transport builds
its own opener that refuses redirects, and a planted test proves the token never reaches
the second host.

Relatedly: `Request(data=...)` silently promotes a GET to a POST. That is the Python
write vector, and it has no PowerShell counterpart - `-Method` was the only way in, and
the guard set was shaped around it.

**What this ADR does not decide.** Every earlier decision stands: the write boundary
(0001), one HTTP layer with two clients (0002), additive by default (0003), fine-grained
tokens only (0004), the authenticated account listing (0005). This changes what the code
is written in, not what it is allowed to do.

In particular, ADR 0004's *reasoning* about a GitHub App weakens and its *decision* does
not. Signing RS256 no longer means hand-parsing PKCS#1 through ASN.1 - but a NaCl sealed
box, which writing an Actions secret requires, is X25519 with XSalsa20-Poly1305, and the
standard library has neither. Pure stdlib still means implementing curve25519 by hand. The
barrier moved from *impossible* to *a dependency question*, and answering that question is
a separate ADR, not a consequence of this one. It would also cross the write boundary ADR
0001 draws.

## How the transition runs

Both implementations live side by side until parity is proven, because **the PowerShell
implementation is the only oracle**. Delete it first and "does the Python port produce the
same answers?" becomes unanswerable. Each pull request leaves both green.

Deletion has a stated trigger, checked before the removal lands:

1. The offline golden comparison green on every CI leg.
2. One live `inventory` and one live `plan` from each implementation, against the same
   account inside one rate-limit window, with an empty report diff outside the normalised
   set - and an identical `declarationFingerprint`, checked first, because if that differs
   they did not read the same declaration and nothing below it means anything.
3. The dual gate green on `ubuntu-latest` and `windows-latest`.

The evidence - the two report filenames and the fingerprint - is recorded in the
`CHANGELOG.md` entry for the removal.

Two implementations is not a maintenance model, only a bounded window. The argument
against it is already written here, in `GitHubAsCode.Http.psm1`: a retry policy
implemented twice is a retry policy that drifts.
