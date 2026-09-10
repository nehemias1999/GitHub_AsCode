# GitHub API behaviours that produce silent damage

**Purpose.** Record the API behaviours where the obvious implementation appears to work
and destroys something, together with what this repository does instead.

**Scope.** REST v3 and GraphQL v4, as used here.

**Audience.** Anyone adding a writer, and anyone debugging a result that looks fine and
is not.

Each row is the same shape: what the API does, what a reasonable person would write,
what that destroys, and the mitigation. Where a row says *measured*, it was observed
against a live API rather than read in the documentation.

**Read the Mitigation column with the phase in mind.** A mitigation that opens with a
phase - **Phase 3**, **Phase 4**, **Phase 5** - describes code that **does not exist
yet**. Everything without a phase is live today and has a test.

That distinction was missing, and nine rows read as present-tense protections that
nothing implemented: a rate-limit preflight, receipts after every operation, an
`apply`-time schema, a GraphQL error classifier, a mutation allowlist. It is the same
failure `AGENTS.md` §7 names, and the one that kept CI broken for thirteen runs while
four documents said the gate ran automatically. The `README.md` command table already
used an "Available: Phase 3" column for exactly this reason; this file simply had not
adopted it.

## The table

### 1. The account listing hides private repositories

| | |
| --- | --- |
| **The API** | `GET /users/{user}/repos` returns **public repositories only**. The private ones exist solely behind `GET /user/repos` with an authenticated token. |
| **The obvious implementation** | Enumerate the account through the endpoint that has the user's name in it. |
| **What it destroys** | Nothing - it destroys *trust*, which is worse here. Every private repository vanishes from an inventory that reports itself complete, and a decision gets made from it. |
| **Mitigation** | `owned_repositories` uses `/user/repos` with `affiliation=owner`. An absence test asserts no string literal anywhere in the repository begins `users/`. See [ADR 0005](../adr/0005-authenticated-account-listing.md). |

### 2. Topics are a replace-the-whole-collection API

| | |
| --- | --- |
| **The API** | `PUT /repos/{owner}/{repo}/topics` replaces the entire collection. There is no per-topic route. |
| **The obvious implementation** | Send the declared topics. |
| **What it destroys** | Every topic somebody added and nobody wrote down. Concretely: whatever sits on a repository nobody has touched in months is exactly what nobody remembers declaring. |
| **Mitigation** | `topic_union` sends the union of live and declared, and reports the undeclared ones as `protected`. Removal is `reconcile`'s job, behind its own confirmation, and `reconcile` does not exist. |

### 3. Branch protection replaces the whole object

| | |
| --- | --- |
| **The API** | `PUT /repos/{owner}/{repo}/branches/{branch}/protection` replaces the entire object and requires every key, with explicit nulls for the ones not wanted. |
| **The obvious implementation** | Send only the field being changed. |
| **What it destroys** | The required status checks, the push restrictions, and the review requirements that were not named - silently, with a 200. |
| **Mitigation** | **Phase 4, and never as an `apply`.** `repo-protection` will print the body a person would have to send and report `manual` / `warning`. See [scope-and-limits.md](../overview/scope-and-limits.md). |

### 4. Admin enforcement plus required reviews locks the owner out

| | |
| --- | --- |
| **The API** | `enforce_admins: true` with `required_approving_review_count >= 1`. |
| **The obvious implementation** | "Let us protect `main` properly." |
| **What it destroys** | `main` becomes **unmergeable**. The author cannot approve their own pull request, and admin enforcement admits no bypass. The only recovery is turning protection off by hand in the web interface. On a single-maintainer account this is not an edge case; it is the default outcome of doing the obvious thing. |
| **Mitigation** | **Phase 4.** `repo-protection`'s `validate` will reject that combination offline, before any token is read. If `apply` ever exists it will use rulesets, whose `bypass_actors` can include the admin role. |

### 5. A contents write overwrites when given a sha

| | |
| --- | --- |
| **The API** | `PUT /repos/{owner}/{repo}/contents/{path}` requires the blob `sha` when the file already exists, and returns 422 when it is omitted. Supplied with a stale `sha`, it overwrites anyway. |
| **The obvious implementation** | Read the file, take its `sha`, send the new content - because the API asked for a `sha`. |
| **What it destroys** | A README somebody wrote by hand, replaced, with the commit attributed to the token. |
| **Mitigation** | **Phase 4.** `sha` will never be sent. The 422 is the *useful* answer: it is the API confirming the file exists, which becomes `update` / `protected`. Double barrier - the plan will not emit a `create` for something that exists, and the API would refuse it if it did. |

### 6. `PATCH /repos` accepts `private` and `archived` as ordinary fields

| | |
| --- | --- |
| **The API** | The same endpoint that sets a description also sets `private`, `archived`, `is_template`, `name` and `default_branch`. |
| **The obvious implementation** | A generic writer that PATCHes whatever the configuration hands it. |
| **What it destroys** | `private` detaches the fork network and disables Pages, irreversibly in the sense that matters. `archived` makes every later write fail, including this tool's own. |
| **Mitigation** | The absence test is **live now**: it asserts no dictionary anywhere in the repository has a key named `private`, `visibility`, `archived`, `is_template` or `default_branch`, which is the shape a request body takes. **Phase 3** adds the other two halves - a metadata schema with `additionalProperties: false` that omits those properties, and an allowlist in the writer. |

### 7. Deleting a label removes it from history

| | |
| --- | --- |
| **The API** | `DELETE /repos/{owner}/{repo}/labels/{name}`. |
| **The obvious implementation** | "Reconcile the labels to match the declaration." |
| **What it destroys** | The label on **every issue and pull request** that carried it, with no record of which ones. |
| **Mitigation** | **Phase 3.** Nothing is deleted. Labels are per-item CRUD - `POST /labels` creates one, `PATCH /labels/{name}` updates one - so additive is the natural shape rather than a workaround. Undeclared labels, including the nine GitHub creates by default, will be `protected`. |

### 8. Renaming a label rewrites the whole issue history

| | |
| --- | --- |
| **The API** | `PATCH /repos/{owner}/{repo}/labels/{name}` with `new_name`. |
| **The obvious implementation** | "Let us normalise the label names." |
| **What it destroys** | The old name across the entire issue and pull request history, in one call, with no batch undo. |
| **Mitigation** | **Phase 3.** `new_name` will not be implemented. Same criterion as a rename in the sibling projects: an operation whose blast radius is not bounded by the plan. |

### 9. GraphQL reports failure with HTTP 200

| | |
| --- | --- |
| **The API** | A GraphQL error comes back as **HTTP 200** with an `errors` array in the body and `data` set to null. |
| **The obvious implementation** | `if ($statusCode -eq 200) { use $data }`. |
| **What it destroys** | A `FORBIDDEN` or `RATE_LIMITED` reads as an empty result, so the plan reports "the project has no fields" instead of "I could not read it" - and the reader acts on a fabricated fact. |
| **Mitigation** | **Phase 5.** `github_as_code.graphql` will classify `errors` **before** looking at `data`, and any error becomes `blocked`. **Measured**: a `projectsV2` query with a token lacking `read:project` returned HTTP 200 carrying `errors[].type = "INSUFFICIENT_SCOPES"`. The fixture is committed as `tests/fixtures/graphql-errors.json` in phase 1, so the trap is written down before the code that must handle it exists. |

### 10. Projects v2 field options are replace-all, and field deletion is total

| | |
| --- | --- |
| **The API** | `updateProjectV2Field` with `singleSelectOptions` replaces the option set. `deleteProjectV2Field` removes the field. |
| **The obvious implementation** | Declare the options wanted. |
| **What it destroys** | Replacing the option set loses the values already assigned on every item. Deleting the field loses them all, with no recycle bin. |
| **Mitigation** | **Phase 5.** Options will be a union, and the `delete*` mutations will not be implemented. The allowlist absence test for `*ProjectV2*` mutation names arrives with that phase; today there is no GraphQL code for it to guard. |

### 11. There are two rate limits, and they behave differently

| | |
| --- | --- |
| **The API** | The primary budget is 5000 requests an hour, visible in `x-ratelimit-remaining`. The **secondary** limits are undocumented ceilings on bursts of writes against one repository, and they answer 403 or 429 with `retry-after`. |
| **The obvious implementation** | Loop the apply over every repository, retry immediately on a 403. |
| **What it destroys** | The run stops halfway, having changed half the repositories, with no record of which half. Retrying immediately escalates a secondary limit into a longer block. |
| **Mitigation** | `retry-after` is honoured **now**, in `retry_decision`. The rest is **phase 3**, when the first writer exists: `minimumWriteIntervalMilliseconds` spacing writes, aborting before starting if `x-ratelimit-remaining` is below `minimumRateLimitRemaining`, and a receipt after every completed operation so an interrupted run is a resume rather than a guess. Those two defaults are declared in `project-context.json` and **read by nothing yet** - deliberately, because a write protection built before the writer is machinery nothing exercises. See [ADR 0001](../adr/0001-write-boundary.md). |

### 12. Pagination does not report a total in the body

| | |
| --- | --- |
| **The API** | The page count arrives in the `Link` header as `rel="last"`. The body is a bare array. The default page size is 30. |
| **The obvious implementation** | Read the first page, or loop `?page=N` until a page comes back short. |
| **What it destroys** | Nothing visibly - which is the problem. A collection that changes while it is being read shifts the offset window, so items are skipped and the truncated list is reported as complete. This is the worst possible failure for the automation whose purpose is completeness. |
| **Mitigation** | `link_header_target` reads `rel="next"` and the loop's only termination condition is its absence. Reaching `maximumPageCount` makes the plan **`blocked`** rather than truncating quietly. Segmentation is driven by the angle brackets, not by splitting on commas, because a comma appears inside query values - `sort=full_name,asc` - and splitting there truncates the URL. |

### 13. A 404 means "absent OR no permission"

| | |
| --- | --- |
| **The API** | GitHub answers 404 both for something that does not exist and for something the token cannot see. It does this deliberately, so a token cannot be used to probe for private resources. |
| **The obvious implementation** | Treat a 404 as absence. |
| **What it destroys** | A plan that says `create` for something that already exists, followed by an apply that fails - or worse, one that succeeds against a name the reader did not expect to be free. |
| **Mitigation** | A declared repository the listing did not return is `resolve` / `blocked`, never `create`, and the reason says both possibilities. The `AllowNotFound` switch exists but its documentation restricts it to cases where absence was established another way. |

### 14. A serialiser that truncates nested objects silently

| | |
| --- | --- |
| **The API** | Not the API - the client, and no longer this one. `ConvertTo-Json` on Windows PowerShell 5.1 defaults to `-Depth 2`. |
| **The obvious implementation** | Serialise without saying how deep. |
| **What it destroys** | Anything nested serialises as the **name of its type**, so the request goes out malformed. Combined with row 9, GraphQL then rejects it with an HTTP 200 - and nothing in the chain reports a problem the reader can act on. |
| **Mitigation** | **Gone with the implementation.** `json.dumps` has no depth limit, so the guard that asserted every `ConvertTo-Json` passed `-Depth` had nothing left to test and was not ported. The row is rewritten rather than removed: this document is the ledger of why each guard exists, and an entry deleted without trace loses the reason. Anyone reintroducing a serialiser with a depth default should read this first. |

### 15. urllib follows redirects and re-sends the Authorization header

| | |
| --- | --- |
| **The API** | Not the API - the client, and only the Python one. `urllib.request` follows a 30x by default and builds the next request from the original headers, `Authorization` included. Unlike some HTTP clients, it does not strip the credential when the redirect crosses to another host. |
| **The obvious implementation** | `urllib.request.urlopen(request)`. |
| **What it destroys** | The credential. A Bearer token with account-wide read access is handed to whatever host the 30x names, with no error, no log line and a 200 at the end of it. This is the single most carefully reasoned decision in the PowerShell transport - `MaximumRedirection = 0` - and a naive port inverts it while looking like a faithful one. |
| **Mitigation** | *Measured.* `src/github_as_code/http.py` builds its own opener with a redirect handler that refuses, and a redirect is reported as the configuration problem it almost always is. `tests/python/test_redirect_refusal.py` runs two servers on the loopback interface and asserts the second is never contacted at all - and, in the same file, that the **stock opener does forward the token**, so the guard is measured rather than assumed. If a future Python starts stripping the header, that second test fails and the reasoning gets reread instead of trusted. |

### 16. `Request(data=...)` silently promotes a GET to a POST

| | |
| --- | --- |
| **The API** | Not the API - the client. `urllib.request.Request` chooses its method from whether a body is present: with `data`, it is a POST, and no method argument is involved. |
| **The obvious implementation** | Add a body to an existing request to send something. |
| **What it destroys** | Whatever a POST to that endpoint creates. It is the Python write vector, and it has no PowerShell counterpart: there, `-Method` was the only way in and the whole guard set was shaped around that one name. |
| **Mitigation** | `tests/python/test_write_boundary.py` reads the parse tree and fails on any call carrying `data`, on any `Request` built with a second positional argument, and on any `method=` that is not the literal `'GET'`. Verified by planting each of those three calls and watching the guard name it. |
