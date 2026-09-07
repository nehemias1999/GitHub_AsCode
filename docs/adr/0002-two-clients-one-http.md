# 0002 - One HTTP layer, two domain clients

**Status.** Accepted, phase 1. `GitHub.GraphQL` arrives in phase 5.

## Context

This repository needs both of GitHub's APIs. REST v3 covers repository metadata,
labels and file contents. **Projects v2 exists only in GraphQL v4** - there is no REST
equivalent, so the choice is not available.

Two inherited rules pull in opposite directions:

- `Invoke-WebRequest` lives in exactly one file, so there is one place to audit and one
  place a write could ever be added.
- The cross-cutting layer carries no domain rules. The moment it grows an
  `if this is GraphQL` branch, it has become a monolith with extra steps.

Two obvious layouts each break one of them. Two independent transports duplicate the
network I/O and the audit surface. One transport that understands both protocols puts
GraphQL's error semantics inside the shared layer.

## Decision

**One `GitHubAsCode.Http`, holding the only `Invoke-WebRequest`, with two thin domain
clients above it.**

The boundary is drawn at a specific place: **the shared layer holds the arithmetic, the
domain client holds the meaning.**

What is genuinely shared is transport, and none of it is protocol-specific: the
authorization header, retry, timeout, the TLS floor, refusing redirects, decoding the
body from raw bytes as UTF-8, and `Get-HttpRetryDecision` - a pure function over
numbers that says whether and how long to wait.

What differs is not I/O at all. It is **how a failure is recognised**:

| | REST v3 | GraphQL v4 |
| --- | --- | --- |
| Addressing | many paths | one endpoint |
| Failure | a status code | **HTTP 200 with an `errors` array** |
| Pagination | `Link` header | `pageInfo.endCursor` |
| Rate limit | requests per hour | **points** per hour, a separate budget |

Every row is a domain rule. `GitHub.Rest` reads `x-ratelimit-remaining` and knows what
the number implies; `GitHub.GraphQL` will inspect `errors` before ever looking at
`data`. Sharing the rate limit counter between them would be straightforwardly
incorrect - they are different budgets, and one does not deplete the other.

## Consequences

Both rules hold. `Invoke-WebRequest` is in one file, and `GitHubAsCode.Http` still
knows nothing about GitHub - no URL, no endpoint, no permission name, no status code
meaning.

The `-StatusMessage` parameter is how that works: the status-code-to-guidance map is
passed **as data** from `GitHub.Rest`, so a 403 can name the missing fine-grained
permission without the transport knowing what a permission is.

The cost is one extra hop, and the discipline of deciding for each new concern which
side of the line it falls on. The rate limit case is the worked example: reading the
header is GitHub knowledge and lives in `GitHub.Rest`; deciding what to do with the
number is arithmetic and lives in `GitHubAsCode.Http`.

`GitHub.GraphQL` is deliberately absent from `Import-Foundation.ps1` until phase 5. A
module listed in the loader before it exists fails the import for every automation
rather than only for the one that needs it.
