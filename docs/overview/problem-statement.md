# The problem

**Purpose.** State what goes wrong with a GitHub account that nobody has ever declared,
and give the commands to measure it — so that later claims about improvement have
something to be measured against.

**Scope.** The GitHub account this repository is pointed at.

**Audience.** Anyone deciding whether this repository is worth its own maintenance.

Deliberately no numbers from any particular account: the point is the method, and a
baseline is only useful if it is yours.

## The shape of it

An account accumulates repositories the way a drawer accumulates cables. Each one made
sense on the day it was made. Collectively:

- Nothing states how any of them **should** be configured, so there is no difference
  between a setting somebody chose and a default nobody turned off.
- The groups are invisible. Tooling, deployable services, learning work and things kept
  for reference all look alike in a list sorted by name.
- Nobody can answer *"is my account set up the way I intend?"* — which is the question
  that matters the morning after somebody asks why a repository has no licence.

## What to measure, and what it tends to show

The table worth having is the one about *your* account, and the commands that produce it
are in the next section. What follows is what that table tends to contain the first time
anybody runs it, and why each row is worth counting.

| What to count | Why it matters |
| --- | --- |
| Repositories owned, total | The denominator for everything else, and the number the wrong endpoint gets wrong |
| Public against **private** | The one that catches people out — see below |
| With at least one topic | Zero means nothing is grouped, and nothing can be found by anything but its name |
| With a licence | Missing on a repository meant to be copied is a real problem, not a tidiness one |
| With `has_projects` / `has_wiki` enabled | Almost certainly the default rather than a decision |
| With a homepage set | Cheap discoverability, almost never used |

### The finding that matters most

**Private repositories appear in no unauthenticated view of an account, and the endpoint
that looks right returns only the public ones.**

An account's profile reports `public_repos`. A search of public repositories returns
roughly that. `GET /user/repos` with a token returns everything the account owns, and the
gap between those two numbers is the whole problem: on an account with a meaningful
number of private repositories, it is a large fraction of the total.

An inventory built on the wrong endpoint is not slightly wrong; it is confidently wrong
about a fraction of its subject, and it says nothing to indicate that. That is worse than
having no inventory at all, because a decision gets made from it.

This is not a hypothetical distinction between two endpoints. It is the measured
difference between `GET /users/{user}/repos` and `GET /user/repos`, and it is why
[ADR 0005](../adr/0005-authenticated-account-listing.md) exists and why an absence test
asserts that no code path builds a `users/` path.

### The rest of it

**Nothing is grouped.** No topics means no way to ask "show me the tooling" or "show me
what I no longer maintain". The groups exist — usually several, accumulated in waves
— but only in the head of the person who made them.

**Defaults were never decided.** Project boards and wikis are enabled because that is what
GitHub creates a repository with. A handful of exceptions is the tell: somebody once
turned one off by hand and then stopped.

**Licences are missing, and missing worst where it matters.** A repository named
*template*, or anything else meant to be copied, with nothing saying whether copying is
allowed.

**The oldest group is a few kilobytes per repository**: a build definition and a README.
No licence, no CI, no `.gitignore`. What to do with repositories like that is a real
decision, and it is deliberately not one this repository makes. It produces the inventory
the decision gets made from.

## Measure your own

Run these before adopting anything here, so the baseline you work against is yours. They
need the `gh` CLI, authenticated. This repository does not depend on `gh` — these are for
establishing the numbers by hand, once.

```bash
# Total, split public and private. The number that matters is the private count:
# if it is not zero, every unauthenticated view of your account is incomplete.
gh api graphql -f query='
{ viewer { repositories(first: 100, ownerAffiliations: OWNER) {
    totalCount
    nodes { name isPrivate isArchived licenseInfo { spdxId }
            hasWikiEnabled hasProjectsEnabled
            repositoryTopics(first: 1) { totalCount } } } } }'
```

```bash
# The same question the wrong way round, to see the gap for yourself.
# The first number is what an unauthenticated view of your account shows.
gh api users/{owner}/repos --paginate --jq 'length'
gh api user/repos --paginate -f affiliation=owner --jq 'length'
```

Then run `inventory` and compare — it should agree with the second number, never the
first:

```bash
python automations/repo-inventory/inventory.py inventory
```

The report writes the four account-level findings — repositories with no licence, with no
topics, and the wiki and projects counts — so the table above becomes a table about your
account.

## What "solved" looks like

A `plan` that reports `pending = 0` and `blocked = 0`, whose `protected` count is
non-zero and **stable between runs**.

That last part is the whole test. A `protected` count that changes between two runs over
an unchanged account means the tool is not reading the account the same way twice, and
nothing it says can be trusted.
