# 0001 - The write boundary, and how it widens

**Status.** Accepted, phase 1.

## Context

`Jenkins_AsCode` is read-only by construction, and can afford an absolute guard: its
HTTP layer has no `-Method` parameter, and a test asserts the word appears nowhere.
`ADO_AsCode` writes, and carries a plan/apply model with confirmations and receipts.

This repository will end up in the second group. It starts in the first.

The tempting shortcut is to build the write machinery now, while the design is fresh,
and leave it unused until phase 3. That is precisely the wrong order: unused write
machinery is machinery nothing tests and nothing guards, sitting one call site away
from an account with 24 repositories on it.

## Decision

**Phases 1 and 2 have no write path at all.** `GitHubAsCode.Http` exposes
`Invoke-ReadOnlyRequest` with the method hard-coded, and the absence test asks the
strongest available question: does `Method` appear as a parameter or a hashtable key
anywhere in the repository? In a repository whose HTTP layer has no such parameter, the
answer must be no, whatever the value would have been.

**Phase 3 widens it deliberately, in one commit, touching four files.** When
`repo-metadata` arrives:

1. `GitHubAsCode.Http` gains `-Method` with
   `[ValidateSet('GET','HEAD','POST','PATCH','PUT')]`. **Never DELETE.**
2. `GitHubAsCode.Plan` gains `Assert-WriteConfirmed`, and no writer may run without it.
3. `GitHubAsCode.Report` gains a receipt written after **every** completed operation,
   so an interrupted run is a resume rather than a guess.
4. The absence test changes: the method guard becomes "every method is a literal from
   the allowlist" instead of "no method exists". The DELETE guard - no `DELETE`, in any
   spelling - **does not change, ever**.

## Consequences

The widening is a reviewable diff in named files rather than a capability that
accreted. Anyone approving that commit can see the whole of what changed about this
repository's relationship to the account.

Until it lands, the strongest possible claim holds: not "it does not write" but "there
is no code path by which it could".

The cost is that phase 3 is a slightly larger commit than it would otherwise be. That
is the intended trade.
