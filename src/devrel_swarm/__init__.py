"""devrel-swarm — DevRel + Sales + Marketing agent system."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("devrel-swarm")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
