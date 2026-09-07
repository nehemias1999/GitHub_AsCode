# The problem

**Purpose.** State what is wrong with the account today, in numbers, so that later
claims about improvement have something to be measured against.

**Scope.** The `nehemias1999` GitHub account.

**Audience.** Anyone deciding whether this repository is worth its own maintenance.

## What was measured

On 2026-09-07, against the live account:

| Fact | Number |
| --- | --- |
| Repositories owned | **24** |
| Public | 15 |
| **Private** | **9** |
| With at least one topic | **0** |
| With a licence | **2** |
| With `has_projects` enabled | 24 |
| With `has_wiki` enabled | 22 |
| With a homepage set | 0 |

## The finding that matters most

The account profile reports `public_repos: 14`. A search of public repositories
returns 15. The authenticated listing returns **24**.

Nine repositories - a third of the account - are invisible to every unauthenticated
view. An inventory built on the wrong endpoint is not slightly wrong; it is confidently
wrong about a third of its subject, and it says nothing to indicate that. That is worse
than having no inventory at all, because a decision gets made from it.

This is not a hypothetical distinction between two endpoints. It is the measured
difference between `GET /users/{user}/repos` and `GET /user/repos`, and it is why
[ADR 0005](../adr/0005-authenticated-account-listing.md) exists and why there is an
absence test asserting no code path builds a `users/` path.

## The rest of it

**Nothing is grouped.** Zero topics across 24 repositories means there is no way to
ask "show me the tooling" or "show me the coursework". The four groups in the table
below are visible only to somebody who already knows the account.

| Class | Count | What it is |
| --- | --- | --- |
| tooling | 4 | `Jenkins_AsCode`, `ADO_AsCode`, `Harness_Basic_Template`, `GitHub_AsCode` |
| pipeline | 5 | Single-purpose Jenkins and Azure pipelines, untouched since February 2026 |
| practice | 6 | Five `Spring_Boot_*` APIs and one university Java project |
| coursework | 9 | Private university work |

**Defaults were never decided.** `has_projects` is true on all 24 and `has_wiki` on 22.
Nobody chose that; it is what GitHub creates a repository with. The two repositories
with the wiki disabled are private, which suggests somebody once turned it off by hand
and then stopped.

**Two of 24 have a licence.** `Harness_Basic_Template` has none, and it is named
*template* - a repository whose whole purpose is to be copied, with nothing saying
whether copying is allowed.

**The five `pipeline` repositories are 4-8 KB each**: a `Jenkinsfile` and a README. No
licence, no CI, no `.gitignore`. What to do with them is a real decision, and it is
deliberately not one this repository makes. It produces the inventory that decision
gets made from.

## What "solved" looks like

A `plan` that reports `pending = 0` and `blocked = 0`, whose `protected` count is
non-zero and **stable between runs**. That last part is the whole test: a protected
count that changes between two runs over an unchanged account means the tool is not
reading the account the same way twice, and nothing it says can be trusted.
