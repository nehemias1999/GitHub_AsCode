# Getting started

**Purpose.** Get from a fresh clone to a plan that reports zero pending.

**Scope.** Phase 1 - `repo-inventory`.

**Audience.** Anyone running this for the first time.

## Prerequisites

Python 3.11 or later, git, and nothing else. There is no package to install to run the
tool, which three guards in the suite prove - one of them by running `validate` with no
`site-packages` at all.

The floor is 3.11 for a concrete reason rather than for novelty, and
[ADR 0006](../adr/0006-python-and-the-standard-library.md) gives it.

Running the **quality gate** needs two development tools, which
`bootstrap.py --check-only` reports on:

```bash
python -m pip install "ruff>=0.6.0,<1.0.0" "jsonschema>=4.0.0,<5.0.0"
```

The upper bound on ruff is deliberate: this repository excludes no lint rule, so a rule
added by a new release fails the gate on code that did not change - a build that breaks
on a day nobody committed. Without `jsonschema` the differential schema check skips
rather than passing, and the run prints every skip with its reason, because a skipped
conformance test and a passing one must not look alike.

## 1. Prepare the workstation

```bash
python scripts/bootstrap.py
```

Checks prerequisites and creates `.env` from `.env.example`.

## 2. Check the declaration, offline

Do this before setting up any credential. It contacts nothing and needs no token, so if
it fails, the problem is in the file and not in your setup.

```bash
python automations/repo-inventory/inventory.py validate
```

With no active declaration yet, it validates the shipped template and says so.

## 3. Create a token

Settings > Developer settings > Personal access tokens > **Fine-grained tokens**.

Not a classic token. The reason is in
[security-model.md](../reference/security-model.md), and it is structural: there is no
fine-grained permission equivalent to deleting a repository, so a fine-grained token
cannot delete one at all.

For phase 1, grant read-only:

- Repository access: **All repositories** (the inventory's job is to be complete)
- Repository permissions: **Metadata: read**, **Contents: read**, **Issues: read**,
  **Administration: read**

Set the shortest expiry you can live with. `inventory` reports the days remaining.

## 4. Fill in `.env`

```
GITHUB_OWNER=your-login
GITHUB_TOKEN_READ=github_pat_...
```

Leave the three write tokens empty. They belong to phases 3 to 5 and nothing reads them
yet.

## 5. Inventory the account

```bash
python automations/repo-inventory/inventory.py inventory
```

Read the summary. **The number that matters is the private count.** If your account has
private repositories and this reports zero, something is reading the public endpoint -
which is the exact failure this automation exists to prevent. Cross-check it:

```bash
gh repo list your-login --limit 200 | wc -l
```

The report lands under `artifacts/repo-inventory/`, as JSON and as Markdown.

## 6. Derive the declaration from the report

Not by hand.

```bash
mv automations/repo-inventory/config/repositories.example.json \
   automations/repo-inventory/config/repositories.json
```

Move it, do not copy it. `repositories.json` is the name `.gitignore` excludes;
copying invites a differently named file full of real repository names that Git happily
tracks.

Then edit it to describe what the report found: one entry per repository, each with the
`class` it belongs to, and the `description` and `topics` it should carry.

## 7. Plan, and get to zero

```bash
python automations/repo-inventory/inventory.py plan
```

What you are aiming for is not "all ok":

```
pending   = 0
blocked   = 0
protected = non-zero, and the SAME number on the next run
```

`pending` items are what `repo-metadata` will change in phase 3. `blocked` items need a
person - most often a declared name that does not exist, or one the token cannot see.
`adopt`/`warning` items are repositories nothing declares yet.

**Run `plan` twice.** That is not a typo. A second run reporting the same operations is
what makes any later finding believable: it proves the inventory and the comparison
agree. If the two runs differ over an unchanged account, the difference is not on
GitHub.

## 8. Run the gate before committing anything

```bash
python scripts/run_tests.py
```

Parse check, ruff, the suite, and the sensitive data scan. CI runs the identical command,
so "it passed locally" and "it passed in CI" mean the same thing.

Every skip is printed with its reason. A check that did not run and a check that passed
must not look alike, which is why the run says which layers of the secret scan answered
rather than reporting an unqualified pass.

## When something goes wrong

[troubleshooting.md](troubleshooting.md).
