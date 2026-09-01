"""GTL - Git-Transform-Load: Sync Git repository history to BigQuery."""

from . import bigquery, git, sync
from .sync import init as init_schema
from .sync import sync as sync_repository

# The convenience functions are deliberately NOT exported as `sync` and `init`.
# Binding a function to `gtl.sync` would shadow the `gtl.sync` submodule, so
# `from gtl import sync` would hand back the function and `gtl.sync.sync_branch`
# would be unreachable by attribute access.
__version__ = "0.1.0"
__all__ = [
    "bigquery",
    "git",
    "sync",
    "init_schema",
    "sync_repository",
    "__version__",
]
