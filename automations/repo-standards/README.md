# repo-standards

**Purpose.** Report which files each repository is missing, judged against the standard
for its class.

**Scope.** Phase 2. Read-only: there is no `apply`, and no code path that could write.

**Audience.** Anyone deciding what to fix first across an account.

## What it does

Every repository belongs to a class - `service`, `library`, `tool`, `archived` - and
every class declares the files a repository of that class must contain. This reads the
contents of each declared repository and reports what is missing, with the reason the
file is required.

It does not create anything. Phase 4 would add an `apply` that creates a missing file
and never overwrites or removes one; until then this is the report a person acts on.

## Where the class comes from, and why not from here

`standards.json` names classes and the files each requires. It does **not** name which
repository is in which class.

That mapping is declared once, in the `repo-inventory` declaration, and read from there.
Two files that must agree about which class a repository is in are two files that drift,
and the drift is silent: a repository would be checked against the wrong standard and
the plan would look entirely reasonable. The project context names both declarations, so
the coupling is declared rather than assumed.

The practical consequence: **every class the inventory declaration uses must appear
here**, and `validate` says so offline if one does not. The other direction is fine - a
standard for a class nothing uses yet is a decision written down early.

## The 404 problem, which is the whole difficulty of this phase

GitHub answers 404 both for a file that does not exist and for one the token cannot see.
For contents that is not a corner case: a fine-grained token with `Metadata: read` and
without `Contents: read` lists every repository perfectly well and then 404s on every
path inside them.

Reading those as absence would produce a plan saying twenty repositories lack a README,
with nothing anywhere saying the run could not look.

So the repository root is read first, once, and it decides everything else:

| | |
| --- | --- |
| The root listing succeeds | Contents are readable, and a 404 below it **is** absence |
| The root listing 404s | `resolve` / `blocked`, and **nothing** is said about the files |

Check `finding.unreadableCount` in the report before reading any file verdict. Anything
above zero means the token is short a permission, and the verdicts below it are absent
rather than wrong.

## Configuration

Rename the template; do not copy it. The active name is the one `.gitignore` excludes.

```bash
mv config/standards.example.json config/standards.json
```

Then describe each class:

```json
{
  "classes": {
    "service": {
      "description": "A deployable service.",
      "requiredFiles": [
        { "path": "README.md", "reason": "what it is and how to run it" }
      ]
    }
  }
}
```

`reason` is printed in the plan, so an approver reads why a file is required rather than
only its path. A class with an empty `requiredFiles` is a decision - `archived` is
exactly that, because an archived repository refuses every write and a missing file
would become a pending operation that can never be applied.

## Commands

```bash
python automations/repo-standards/standards.py validate
python automations/repo-standards/standards.py inventory
python automations/repo-standards/standards.py plan
python automations/repo-standards/standards.py smoke
```

| Command | Reads live state | Writes |
| --- | --- | --- |
| `validate` | No | No |
| `inventory` | Yes | No |
| `plan` | Yes | No |
| `smoke` | Yes | No |

`--repository-name` restricts the run and records the scope in the report, because a
filtered run and a whole one otherwise differ only in a total.

## Permissions

`GITHUB_TOKEN_READ`, with **`Contents: read`** in addition to the set `repo-inventory`
needs. Without it every repository comes back `blocked` - which is the correct answer
rather than a silent one.

## Cost

One request per repository, plus one per distinct directory the standards name. Not one
per declared file: files are checked against a directory listing that was already
fetched. For twenty repositories and a standard naming files in the root and `.github`,
that is forty requests against a budget of five thousand an hour.

## Output

`artifacts/reports/repo-standards-<command>-<timestamp>Z-<id>.json`, and a Markdown
sibling. `finding.missingByPath` counts how many repositories are missing each file,
which is the number that decides what to do first - usually a licence.

## Rollback

Nothing to roll back: this automation writes nothing to GitHub. To undo a run, delete
its report under `artifacts/`, which is excluded from version control. To undo a change
to the declaration, revert the file.

When phase 4 adds `apply`, rollback becomes "delete the file it created", and the guide
gains the commit SHA of every file written so that is possible.
