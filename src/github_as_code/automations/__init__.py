"""Orchestration and reporting, one module per resource family.

An automation reuses the foundation and adds nothing domain-specific to it. The moment
one of these needs a special case in a shared module, the logic belongs here instead:
the shared layer holds the arithmetic, the domain client holds the meaning, and this
layer holds the order things happen in and what gets said about them.

See docs/reference/architecture.md.
"""
