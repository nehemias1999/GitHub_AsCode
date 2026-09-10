# The command model

**Purpose.** Define the verbs and the two status axes, once, so every automation means
the same thing by them.

**Scope.** Every entry point under `automations/`.

**Audience.** Anyone reading a plan, and anyone adding an automation.

## The ladder

Each rung does everything the one above it does, and more.

| Command | Reads live state | Writes | Confirmation | Available now |
| --- | --- | --- | --- | --- |
| `validate` | No | No | - | Yes |
| `inventory` | Yes | No | - | Yes |
| `plan` | Yes | No | - | Yes |
| `smoke` | Yes | No | - | Yes |
| `apply` | Yes | **Yes** | `-ConfirmApply` | Phase 3 |
| `reconcile` | Yes | **Yes** | `-ConfirmApply -ConfirmReconcile` | Perhaps never |

`validate` is offline and needs no token. That is a hard requirement rather than a
convenience: it means a malformed declaration fails in a second, and it means CI can
exercise every automation's template with no credential in the environment at all.

What must **not** happen is a generic verb loaded with selectors that change its
meaning. One command, one intent.

## Two axes, not one

Defined in `src/github_as_code/plan.py`, and reused
verbatim from the sibling projects rather than reinvented. Confusing the two axes is the
easiest mistake to make here, so they are stated separately.

### Status - may this proceed?

| Status | Meaning |
| --- | --- |
| `ok` | The live state already matches. Nothing to do. |
| `pending` | A change is required, and it is safe to make. |
| `warning` | The change will proceed, but a person should read the reason. |
| `protected` | **Deliberately not changed, to avoid destroying something.** |
| `blocked` | Nothing further about this resource could be determined. |

### Action - what would happen to the resource?

`create` · `exists` · `adopt` · `update` · `set` · `add` · `reconcile` · `rename` ·
`authorize` · `validate` · `resolve` · `manual` · `skip`

## `manual` and `skip` are actions, not statuses

This is the distinction worth stating twice, because getting it wrong produces a plan
that reads plausibly and means something else.

`manual` is an **action**: a person performs it. `protected` is a **status**: the tool
looked, decided that changing it would destroy something, and left it alone.

So "the file exists but differs from the template" is `update` / `protected`, not
`manual`. The action that *would* apply is an update; the status records that it was
withheld. Reporting it as `manual` would tell the reader a person has to do something,
when in fact nothing may need doing at all.

## What a clean plan looks like

Not "all `ok`".

```
pending   = 0
blocked   = 0
protected = some non-zero number, the SAME number as the previous run
```

A stable non-zero `protected` count is a healthy account: it is the tool consistently
declining to remove things nobody declared. A `protected` count that moves between two
runs over an unchanged account means the tool is not reading the account the same way
twice, and nothing else it reports can be trusted.

## Why reasons are prose

`pending` on its own is not reviewable. Every operation carries a reason written for the
person approving the plan, not for a log parser - which is why
`repository_status` names the fields that differ rather than emitting a count.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The run completed and nothing is blocked |
| `2` | The run completed and at least one resource could not be determined |
| `1` | The run itself failed - a bad declaration, no credential, the API unreachable |

`2` exists because the result has a consumer that is not a person reading a screen. A
scheduled run whose plan was entirely blocked must not report success.
