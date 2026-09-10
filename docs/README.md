# Documentation

Routed by need, not by folder.

| If you want to | Read |
| --- | --- |
| **Start here, if you generated this from the template** | [using-this-template.md](guides/using-this-template.md) |
| Run it | [getting-started.md](guides/getting-started.md) |
| Know why this exists | [problem-statement.md](overview/problem-statement.md) |
| Know exactly what it does | [capabilities.md](overview/capabilities.md) |
| Know what it refuses to do, and why | [scope-and-limits.md](overview/scope-and-limits.md) |
| **Fix something that went wrong** | [troubleshooting.md](guides/troubleshooting.md) |
| Inventory the account | [repo-inventory guide](../automations/repo-inventory/README.md) |
| See which files repositories are missing | [repo-standards guide](../automations/repo-standards/README.md) |
| Understand the layers | [architecture.md](reference/architecture.md) |
| Understand the verbs and statuses | [command-model.md](reference/command-model.md) |
| Add an automation | [automation-contract.md](reference/automation-contract.md) |
| Choose and scope a token | [security-model.md](reference/security-model.md) |
| **Learn the API traps** | [github-notes.md](reference/github-notes.md) |
| Know how it is tested | [testing-strategy.md](process/testing-strategy.md) |
| **Read how the port ran, and what it found** | [port-status.md](process/port-status.md) |

## Decisions

Each ADR records a decision that would be expensive to reverse, and the reason.

| ADR | Decision |
| --- | --- |
| [0001](adr/0001-write-boundary.md) | The write boundary, and how it widens |
| [0002](adr/0002-two-clients-one-http.md) | One HTTP layer, two domain clients |
| [0003](adr/0003-additive-by-default.md) | Additive by default: nothing is ever deleted |
| [0004](adr/0004-fine-grained-tokens-only.md) | Fine-grained tokens, because they cannot delete |
| [0005](adr/0005-authenticated-account-listing.md) | The account listing endpoint |
| [0006](adr/0006-python-and-the-standard-library.md) | Python, and nothing but its standard library |
| [0007](adr/0007-one-schema-validator.md) | One schema validator, built in and guarded for coverage |

Every document here is linked from this page, and the test suite fails if one is not:
a document nobody can reach from the index is one nobody reads, and it drifts from the
code silently.
