"""Declarative, read-only inventory of a GitHub account.

The Python half of the port ADR 0006 decides. It is empty of behaviour so far: this
package exists from the first commit of the port so the guards have something real to
read, rather than passing over an empty tree and reporting the same green as a tree
they actually checked.

Nothing here may import anything outside the standard library. See
docs/adr/0006-python-and-the-standard-library.md, and the three guards under
tests/python/ that enforce it in three different ways.
"""

# Kept in step with the version in pyproject.toml by hand. There is deliberately no
# importlib.metadata lookup: that reads installed distribution metadata, and this
# package is never installed - it is run from src/ on sys.path, which is what lets the
# execution guard import it with no site-packages at all.
__version__ = "0.1.0"

__all__ = ["__version__"]
