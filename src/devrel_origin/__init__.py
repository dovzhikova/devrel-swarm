"""devrel-origin — DevRel + Sales + Marketing agent system."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("devrel-origin")
except PackageNotFoundError:
    # Backwards-compat: pre-0.2.14 PyPI distributions were named
    # "devrel-swarm". If a user is on the old wheel but the new code
    # path (e.g. mid-upgrade or editable install pointing at this tree),
    # fall back so __version__ still resolves.
    try:
        __version__ = version("devrel-swarm")
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"
