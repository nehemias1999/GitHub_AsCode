# GitHub API behaviours that produce silent damage

**Purpose.** Record the API behaviours where the obvious implementation appears to work
and destroys something, together with what this repository does instead.

**Scope.** REST v3 and GraphQL v4, as used here.

**Audience.** Anyone adding a writer, and anyone debugging a result that looks fine and
is not.

Each row is the same shape: what the API does, what a reasonable person would write,
what that destroys, and the mitigation. Where a row says *measured*, it was observed
against the live account on 2026-09-07 rather than read in documentation.

## The table

### 1. The account listing hides private repositories

| | |
| --- | --- |
| **The API** | `GET /users/{user}/repos` returns **public repositories only**. The private ones exist solely behind `GET /user/repos` with an authenticated token. |
| **The obvious implementation** | Enumerate the account through the endpoint that has the user's name in it. |
| **What it destroys** | Nothing - it destroys *trust*, which is worse here. **Measured: the public endpoint returns 15 repositories and the authenticated one returns 24.** Nine repositories vanish from an inventory that reports itself complete, and a decision gets made from it. |
| **Mitigation** | `Get-GitHubOwnedRepository` uses `/user/repos` with `affiliation=owner`. An absence test asserts no string literal anywhere in the repository begins `users/`. See [ADR 0005](../adr/0005-authenticated-account-listing.md). |

### 2. Topics are a replace-the-whole-collection API

| | |
| --- | --- |
| **The API** | `PUT /repos/{owner}/{repo}/topics` replaces the entire collection. There is no per-topic route. |
| **The obvious implementation** | Send the declared topics. |
| **What it destroys** | Every topic somebody added and nobody wrote down. Concretely: the five `pipeline` repositories have not been touched since February 2026, so whatever is on them is exactly what nobody remembers declaring. |
| **Mitigation** | `Get-GitHubTopicUnion` sends the union of live and declared, and reports the undeclared ones as `protected`. Removal is `reconcile`'s job, behind its own confirmation, and `reconcile` does not exist. |

### 3. Branch protection replaces the whole object

| | |
| --- | --- |
| **The API** | `PUT /repos/{owner}/{repo}/branches/{branch}/protection` replaces the entire object and requires every key, with explicit nulls for the ones not wanted. |
| **The obvious implementation** | Send only the field being changed. |
| **What it destroys** | The required status checks, the push restrictions, and the review requirements that were not named - silently, with a 200. |
| **Mitigation** | Not implemented. `repo-protection` prints the body a person would have to send and reports `manual` / `warning`. See [scope-and-limits.md](../overview/scope-and-limits.md). |

### 4. Admin enforcement plus required reviews locks the owner out

| | |
| --- | --- |
| **The API** | `enforce_admins: true` with `required_approving_review_count >= 1`. |
| **The obvious implementation** | "Let us protect `main` properly." |
| **What it destroys** | `main` becomes **unmergeable**. The author cannot approve their own pull request, and admin enforcement admits no bypass. The only recovery is turning protection off by hand in the web interface. On a single-maintainer account this is not an edge case; it is the default outcome of doing the obvious thing. |
| **Mitigation** | `validate` rejects that combination **offline**, before any token is read. If `apply` ever exists it will use rulesets, whose `bypass_actors` can include the admin role. |

### 5. A contents write overwrites when given a sha

| | |
| --- | --- |
| **The API** | `PUT /repos/{owner}/{repo}/contents/{path}` requires the blob `sha` when the file already exists, and returns 422 when it is omitted. Supplied with a stale `sha`, it overwrites anyway. |
| **The obvious implementation** | Read the file, take its `sha`, send the new content - because the API asked for a `sha`. |
| **What it destroys** | A README somebody wrote by hand, replaced, with the commit attributed to the token. |
| **Mitigation** | `sha` is never sent. The 422 is the *useful* answer: it is the API confirming the file exists, which becomes `update` / `protected`. Double barrier - the plan does not emit a `create` for something that exists, and the API would refuse it if it did. |

### 6. `PATCH /repos` accepts `private` and `archived` as ordinary fields

| | |
| --- | --- |
| **The API** | The same endpoint that sets a description also sets `private`, `archived`, `is_template`, `name` and `default_branch`. |
| **The obvious implementation** | A generic writer that PATCHes whatever the configuration hands it. |
| **What it destroys** | `private` detaches the fork network and disables Pages, irreversibly in the sense that matters. `archived` makes every later write fail, including this tool's own. |
| **Mitigation** | The metadata schema declares `additionalProperties: false` and does not contain those properties; the writer has an allowlist; and an absence test asserts no hashtable anywhere in the repository has a key of those names - which is the shape a request body takes. |

### 7. Deleting a label removes it from history

| | |
| --- | --- |
| **The API** | `DELETE /repos/{owner}/{repo}/labels/{name}`. |
| **The obvious implementation** | "Reconcile the labels to match the declaration." |
| **What it destroys** | The label on **every issue and pull request** that carried it, with no record of which ones. |
| **Mitigation** | Nothing is deleted. Labels are per-item CRUD - `POST /labels` creates one, `PATCH /labels/{name}` updates one - so additive is the natural shape rather than a workaround. Undeclared labels, including the nine GitHub creates by default, are `protected`. |

### 8. Renaming a label rewrites the whole issue history

| | |
| --- | --- |
| **The API** | `PATCH /repos/{owner}/{repo}/labels/{name}` with `new_name`. |
| **The obvious implementation** | "Let us normalise the label names." |
| **What it destroys** | The old name across the entire issue and pull request history, in one call, with no batch undo. |
| **Mitigation** | `new_name` is not implemented. Same criterion as `rename` in `ADO_AsCode`: an operation whose blast radius is not bounded by the plan. |

### 9. GraphQL reports failure with HTTP 200

| | |
| --- | --- |
| **The API** | A GraphQL error comes back as **HTTP 200** with an `errors` array in the body and `data` set to null. |
| **The obvious implementation** | `if ($statusCode -eq 200) { use $data }`. |
| **What it destroys** | A `FORBIDDEN` or `RATE_LIMITED` reads as an empty result, so the plan reports "the project has no fields" instead of "I could not read it" - and the reader acts on a fabricated fact. |
| **Mitigation** | `GitHub.GraphQL` classifies `errors` **before** looking at `data`; any error becomes `blocked`. **Measured**: a `projectsV2` query with a token lacking `read:project` returned HTTP 200 carrying `errors[].type = "INSUFFICIENT_SCOPES"`. The fixture is committed as `tests/fixtures/graphql-errors.json`, in phase 1, so the trap is written down before the code that must handle it exists. |

### 10. Projects v2 field options are replace-all, and field deletion is total

| | |
| --- | --- |
| **The API** | `updateProjectV2Field` with `singleSelectOptions` replaces the option set. `deleteProjectV2Field` removes the field. |
| **The obvious implementation** | Declare the options wanted. |
| **What it destroys** | Replacing the option set loses the values already assigned on every item. Deleting the field loses them all, with no recycle bin. |
| **Mitigation** | Options are a union. The `delete*` mutations are not implemented, and an absence test asserts every `*ProjectV2*` mutation name appearing as a literal is on an allowlist. |

### 11. There are two rate limits, and they behave differently

| | |
| --- | --- |
| **The API** | The primary budget is 5000 requests an hour, visible in `x-ratelimit-remaining`. The **secondary** limits are undocumented ceilings on bursts of writes against one repository, and they answer 403 or 429 with `retry-after`. |
| **The obvious implementation** | Loop the apply over 24 repositories, retry immediately on a 403. |
| **What it destroys** | The run stops halfway, having changed half the repositories, with no record of which half. Retrying immediately escalates a secondary limit into a longer block. |
| **Mitigation** | `retry-after` is honoured; `minimumWriteIntervalMilliseconds` spaces writes; the run aborts **before starting** if `x-ratelimit-remaining` is below the threshold; and a receipt is written after **every** completed operation, so an interrupted run is a resume rather than a guess. |

### 12. Pagination does not report a total in the body

| | |
| --- | --- |
| **The API** | The page count arrives in the `Link` header as `rel="last"`. The body is a bare array. The default page size is 30. |
| **The obvious implementation** | Read the first page, or loop `?page=N` until a page comes back short. |
| **What it destroys** | Nothing visibly - which is the problem. A collection that changes while it is being read shifts the offset window, so items are skipped and the truncated list is reported as complete. This is the worst possible failure for the automation whose purpose is completeness. |
| **Mitigation** | `Get-HttpLinkHeaderTarget` reads `rel="next"` and the loop's only termination condition is its absence. Reaching `maximumPageCount` makes the plan **`blocked`** rather than truncating quietly. Segmentation is driven by the angle brackets, not by splitting on commas, because a comma appears inside query values - `sort=full_name,asc` - and splitting there truncates the URL. |

### 13. A 404 means "absent OR no permission"

| | |
| --- | --- |
| **The API** | GitHub answers 404 both for something that does not exist and for something the token cannot see. It does this deliberately, so a token cannot be used to probe for private resources. |
| **The obvious implementation** | Treat a 404 as absence. |
| **What it destroys** | A plan that says `create` for something that already exists, followed by an apply that fails - or worse, one that succeeds against a name the reader did not expect to be free. |
| **Mitigation** | A declared repository the listing did not return is `resolve` / `blocked`, never `create`, and the reason says both possibilities. The `AllowNotFound` switch exists but its documentation restricts it to cases where absence was established another way. |

### 14. PowerShell 5.1 truncates JSON at depth 2

| | |
| --- | --- |
| **The API** | Not the API - the client. `ConvertTo-Json` on Windows PowerShell 5.1 defaults to `-Depth 2`. |
| **The obvious implementation** | `$variables \| ConvertTo-Json`. |
| **What it destroys** | Anything nested serialises as the **name of its type**, so the request goes out malformed. Combined with row 9, GraphQL then rejects it with an HTTP 200 - and nothing in the chain reports a problem the reader can act on. |
| **Mitigation** | `-Depth` is always explicit, and an absence test walks the parse tree asserting no `ConvertTo-Json` call omits it. |
