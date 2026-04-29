"""
DevTools Advocate Agent System

A multi-agent system for autonomous developer advocacy,
built on Claude Agent SDK and Model Context Protocol (MCP).
"""

from devrel_swarm.core.atlas import Atlas
from devrel_swarm.core.dex import Dex
from devrel_swarm.core.echo import Echo
from devrel_swarm.core.iris import Iris
from devrel_swarm.core.kai import Kai
from devrel_swarm.core.mox import Mox
from devrel_swarm.core.nova import Nova
from devrel_swarm.core.pax import Pax
from devrel_swarm.core.rex import Rex
from devrel_swarm.core.sage import Sage
from devrel_swarm.core.sentinel import Sentinel
from devrel_swarm.core.vox import Vox
from devrel_swarm.core.watchdog import Watchdog

__all__ = [
    "Atlas", "Dex", "Echo", "Kai", "Mox", "Sage",
    "Iris", "Nova", "Pax", "Rex", "Sentinel", "Vox", "Watchdog",
]
