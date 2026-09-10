# repo-inventory

Reads every repository the account owns - **public and private** - and reports how live
state differs from the declaration. It cannot write.

```bash
.\inventory.py -Command validate     # offline, no token
.\inventory.py -Command inventory    # what exists today
.\inventory.py -Command plan         # declared vs live
```

## Why it exists

Because the endpoint that looks right is wrong. `GET /users/{user}/repos` returns public
repositories only. On the account where this was first measured that hid **a third of the
total** - every private repository missing, with nothing in the response indicating
anything is absent.

So this reads `GET /user/repos` with `affiliation=owner`, and there is a repository-wide
absence test asserting no code path builds a `users/` path. See
[ADR 0005](../../docs/adr/0005-authenticated-account-listing.md).

The output is the input to a decision: which of the older repositories are worth
keeping, standardising or archiving. That decision is deliberately not this tool's to
make - it produces the evidence, a person makes the call.

## Commands

| Command | Reads live state | Writes | Needs a token |
| --- | --- | --- | --- |
| `validate` | No | No | **No** |
| `inventory` | Yes | No | Yes, read-only |
| `plan` | Yes | No | Yes, read-only |
| `smoke` | Yes | No | Yes, read-only |

There is no `apply`, and no code path that could write:
`github_as_code.http` is the only file that imports `urllib.request`, and an absence test
asserts no call anywhere passes a body and every `method=` is the literal `'GET'`.

## Configuration

Template: `config/repositories.example.json`. Schema:
`schemas/repositories.schema.json`.

Produce the active file by **renaming**:

```bash
Move-Item config\repositories.example.json config\repositories.json
```

Move it, do not copy it. `repositories.json` is the name `.gitignore` excludes; a
copy invites a differently named file full of real repository names that Git happily
tracks - and on an account with private repositories, the names alone are worth
excluding.

### Fields

| Field | Required | Notes |
| --- | --- | --- |
| `classes` | yes | The classes a repository may belong to, each with a description. Declared rather than inferred from the name: a rule that guesses which class a repository is in will one day guess wrong about the one repository where it matters. |
| `repositories[].name` | yes | The repository name only, never `owner/repo` - the owner comes from `GITHUB_OWNER`. |
| `repositories[].class` | yes | Must be a key of `classes`. Checked by `validate`, because JSON Schema cannot express a cross-reference within one document. |
| `repositories[].description` | no | At most 350 characters. GitHub truncates beyond that, so a longer value could never compare equal and every plan would report the same change forever. `validate` catches it offline. |
| `repositories[].homepage` | no | An empty string means "none". The API returns an unset homepage as `""` and an unset description as `null`, inconsistently; both are normalised before comparison. |
| `repositories[].topics` | no | **Added to** whatever is already live, never substituted for it. `PUT /topics` replaces the whole collection, so sending only these would delete every topic added by hand. Uppercase is fine and normalised; `c#` or a space is rejected rather than mangled. |

### How to write it

Not by hand. Run `inventory`, read the `repository` array in the report, and derive the
declaration from what was actually found. Then `plan` against the same account must
report zero pending - and that zero is the proof that the inventory and the comparison
agree, which is what makes any later finding believable.

## Permissions

`GITHUB_TOKEN_READ`, a **fine-grained** token (see
[ADR 0004](../../docs/adr/0004-fine-grained-tokens-only.md)):

- Repository access: **all repositories** - the job here is completeness
- Metadata: read · Contents: read · Issues: read · Administration: read

## Output

| What | Where |
| --- | --- |
| Report, machine-readable | `artifacts/repo-inventory/*.json` |
| Report, readable | `artifacts/repo-inventory/*.md` |
| Run transcript | the `runLog` path named in the report |

The report carries provenance - which command, which declaration, which schema engine,
what scope - because a filtered run and a whole one otherwise differ only in a total,
and `pending 0` then reads as "everything is aligned" when it could equally mean "one
repository was examined".

It also carries the four account-level findings: which repositories have no licence,
which have no topics, and how many have wikis and projects enabled.

## What the statuses mean here

| Situation | action | status |
| --- | --- | --- |
| Declared, live, matches | `exists` | `ok` |
| Declared, live, differs | `update` | `pending` |
| Declared, **not returned by the API** | `resolve` | `blocked` |
| Declared, live, archived | `skip` | `protected` |
| Live, **not declared** | `adopt` | `warning` |
| Account listing truncated | `resolve` | `blocked` |

A declared repository the API did not return is **never** reported as `create`. GitHub
answers 404 both for something that does not exist and for something the token cannot
see, so the tool genuinely does not know which - and `blocked` is what "could not be
determined" means.

On an account nobody has declared, every repository lands in the `adopt` row. That is
the finding of a first run, not a fault in it.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Nothing blocked |
| `2` | Something could not be determined - a declared repository the API did not return, or a truncated listing |
| `1` | The run itself failed |

## Rollback

**Nothing to reverse: this automation writes nothing to GitHub.** No repository, no
setting, no file, and no code path by which it could.

What a run does leave behind, all of it local and all of it excluded from version
control:

| Artefact | To undo |
| --- | --- |
| Reports under `artifacts/repo-inventory/` | Delete them. Nothing reads a previous report |
| The run transcript under `artifacts/` | Delete it |
| `config/repositories.json`, if you created it | Delete it. The automation falls back to the versioned template and says so in a warning |

The only change with any reach outside this clone is the read itself, which consumes
rate limit budget. It replenishes hourly, and the report tells you what was left.
