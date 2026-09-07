# The problem

**Purpose.** State what goes wrong with a GitHub account that nobody has ever declared,
and give the commands to measure it — so that later claims about improvement have
something to be measured against.

**Scope.** The GitHub account this repository is pointed at.

**Audience.** Anyone deciding whether this repository is worth its own maintenance.

## The shape of it

An account accumulates repositories the way a drawer accumulates cables. Each one made
sense on the day it was made. Collectively:

- Nothing states how any of them **should** be configured, so there is no difference
  between a setting somebody chose and a default nobody turned off.
- The groups are invisible. Tooling, deployable services, learning work and things kept
  for reference all look alike in a list sorted by name.
- Nobody can answer *"is my account set up the way I intend?"* — which is the question
  that matters the morning after somebody asks why a repository has no licence.

## Measured on one real account

The numbers below come from a single real account of 24 repositories, and they are here
for one reason: to show that the API traps in
[github-notes.md](../reference/github-notes.md) are things that happen rather than
things that could happen. **Your baseline will be different.** Measure it — the commands
are in the next section.

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

### The finding that matters most

That account's profile reported `public_repos: 14`. A search of public repositories
returned 15. The authenticated listing returned **24**.

Nine repositories — a third of the account — were invisible to every unauthenticated
view. An inventory built on the wrong endpoint is not slightly wrong; it is confidently
wrong about a third of its subject, and it says nothing to indicate that. That is worse
than having no inventory at all, because a decision gets made from it.

This is not a hypothetical distinction between two endpoints. It is the measured
difference between `GET /users/{user}/repos` and `GET /user/repos`, and it is why
[ADR 0005](../adr/0005-authenticated-account-listing.md) exists and why an absence test
asserts that no code path builds a `users/` path.

### The rest of it

**Nothing was grouped.** Zero topics across 24 repositories means there is no way to ask
"show me the tooling" or "show me the things I no longer maintain". The groups existed —
four of them, accumulated in waves — but only in the head of the person who made them.

**Defaults were never decided.** `has_projects` was true on all 24 and `has_wiki` on 22.
Nobody chose that; it is what GitHub creates a repository with. The two with the wiki
disabled were the tell: somebody once turned it off by hand and then stopped.

**Two of 24 had a licence.** One of the ones without it was named *template* — a
repository whose whole purpose is to be copied, with nothing saying whether copying is
allowed.

**The oldest group was 4–8 KB per repository**: a build definition and a README. No
licence, no CI, no `.gitignore`. What to do with repositories like that is a real
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

```powershell
.\automations\repo-inventory\Invoke-RepositoryInventory.ps1 -Command inventory
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
