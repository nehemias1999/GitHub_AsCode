# Scope and limits

**Purpose.** Name every operation this repository refuses to perform, and give the
reason for each. A refusal without a reason gets removed by the next person.

**Scope.** All automations, present and planned.

**Audience.** Anyone about to add a feature here, and anyone wondering why an obvious
one is missing.

## The principle

Some operations have a blast radius that is not described by the plan that authorises
them. For those, this repository reports what a person would have to do, and does not
do it. Shipping such a feature is worse than omitting it: its successful path is a
silent failure, and it gets trusted because it usually works.

## Never implemented, at any phase

| Operation | Why not |
| --- | --- |
| **Delete a repository** | Irreversible, and not recoverable from a plan. The recommended token type *cannot* do it - see [ADR 0004](../adr/0004-fine-grained-tokens-only.md). An absence test asserts the `delete_repo` scope name appears nowhere except the warning that refuses to proceed with it. |
| **Change visibility** | Public to private detaches the fork network, discards the stars and watchers relationship, and disables Pages. Flipping the flag back restores none of it. It is irreversible in the sense that matters, which is not the sense the API suggests. |
| **Archive or unarchive** | An archived repository is read-only, so every later write against it fails - including this tool's own. `inventory` reports archived state; nothing acts on it. It is also the specific decision pending about the five `pipeline` repositories, and that decision belongs to a person reading the inventory. |
| **Rename a repository** | Breaks every clone, every submodule reference and every link. GitHub redirects for a while, then stops. |
| **Transfer ownership** | Irreversible without the recipient's cooperation. |
| **Change the default branch** | The blast radius reaches every open pull request and every CI configuration keyed to the branch name. |
| **Delete a label** | Removes it from every issue and pull request that carried it, with no record of which. |
| **Rename a label** | A `PATCH` with `new_name` rewrites the label across the entire issue history in one call, and there is no batch undo. |
| **Delete a Projects v2 field** | Deletes that field's value on every item in the project. There is no recycle bin. |
| **Write an Actions secret or variable** | A secret must be encrypted with a NaCl sealed box against the repository public key. The standard library has neither X25519 nor XSalsa20-Poly1305, so a pure-standard-library implementation means writing curve25519 by hand - and it would violate *names, not values* - this repository's configuration declares the name of a variable and never its content. |
| **Manage collaborators, webhooks, Pages or Dependabot config** | Access grants and secret carriers. Different review standard, different repository. |

## Not implemented because the API cannot

| Operation | Why not |
| --- | --- |
| **Create or configure a Projects v2 view** | The GraphQL API has no mutation for it. This is the cleanest possible case of "not implemented because it is not possible": the plan reports `manual` and says so, and will keep saying so unless GitHub adds one. |

## Plan-only by design

`repo-protection` will read branch protection and rulesets and will **never** have an
`apply`, in any phase. Three reasons, and the middle one is decisive:

1. `PUT /repos/{owner}/{repo}/branches/{branch}/protection` replaces the whole object
   and requires every key, with explicit nulls. Sending only the field being changed
   silently discards the required status checks, restrictions and reviews that were not
   named.

2. **`enforce_admins: true` together with `required_approving_review_count >= 1` on a
   single-owner repository makes `main` unmergeable.** The author cannot approve their
   own pull request, and admin enforcement admits no bypass. The only recovery is
   turning protection off by hand in the web interface. A tool that can put an account
   in that state, from a form, is not a safe tool - so `validate` rejects that
   combination offline rather than waiting to find out.

3. There is no declarative rollback if the token loses `administration` access
   afterwards.

If protection ever does get an `apply`, it will be through rulesets only:
`POST /repos/{owner}/{repo}/rulesets` creates a new named object without touching
existing protection, and `bypass_actors` can include the admin role - so the owner
cannot lock themselves out.

## Additive, never subtractive

Every writer this repository ever gains is additive. Where a collection is involved,
the payload is the union of live and declared, and the undeclared members are reported
as `protected` rather than removed. See [ADR 0003](../adr/0003-additive-by-default.md).

The one exception, if it is ever built, is `reconcile` on `repo-metadata` - the only
operation that would remove a declared-away topic. It will require
`--confirm-apply --confirm-reconcile`, and it will be absent from every workflow
definition, for the same reason a rename is absent from the sibling projects' pipeline
definitions: its blast radius is not bounded by the plan.
