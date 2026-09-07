# 0003 - Additive by default: nothing is ever deleted

**Status.** Accepted, phase 1.

## Context

Several GitHub collections are replace-the-whole-thing APIs. `PUT /repos/{o}/{r}/topics`
has no per-topic route; branch protection replaces its entire object; Projects v2 field
options replace their whole set.

The declarative instinct says: the declaration is the desired state, so make live state
match it. Applied to those endpoints, that instinct deletes things.

It deletes them where it hurts most, too. The declaration is written by someone reading
an inventory *today*; the topics on a repository nobody has touched since February were
added by someone at the time, for a reason nobody wrote down. The declaration's silence
about them is not a decision that they should go.

## Decision

**Every writer is additive. Nothing is deleted.**

For a collection, the payload is the **union** of live and declared, and the members
that are live but undeclared are reported as `protected` with a reason - not kept
silently. Keeping them without saying so is nearly as bad: the reader cannot then tell a
preserved member from a declared one.

Removal exists as exactly one operation, `reconcile`, and it:

- requires `-ConfirmApply -ConfirmReconcile`
- is absent from every workflow definition, because its blast radius is not bounded by
  its plan
- does not exist yet, and may never

## Consequences

Convergence is one-directional. This tool can bring an account up to a declaration and
cannot bring it back down, so a declaration that shrinks has no effect until somebody
runs `reconcile` or edits by hand. That is the right asymmetry for a tool that runs
unattended.

A clean plan is therefore **not** all `ok`. It is `pending = 0`, `blocked = 0`, and a
`protected` count that is non-zero and **stable between runs**. The stability is the
real assertion: a `protected` count that moves over an unchanged account means the tool
is not reading the account the same way twice, and nothing else it reports can be
trusted.

`Get-GitHubTopicUnion` is where this lives for topics, and it is the most heavily
tested function in the repository - including an explicit assertion that applying the
same declaration to the payload it just produced changes nothing.
