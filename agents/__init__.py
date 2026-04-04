"""
DevTools Advocate Agent System

A multi-agent system for autonomous developer advocacy,
built on Claude Agent SDK and Model Context Protocol (MCP).
"""

from agents.atlas import Atlas
from agents.dex import Dex
from agents.echo import Echo
from agents.iris import Iris
from agents.kai import Kai
from agents.mox import Mox
from agents.nova import Nova
from agents.pax import Pax
from agents.rex import Rex
from agents.sage import Sage
from agents.sentinel import Sentinel
from agents.vox import Vox
from agents.watchdog import Watchdog

__all__ = [
    "Atlas", "Dex", "Echo", "Kai", "Mox", "Sage",
    "Iris", "Nova", "Pax", "Rex", "Sentinel", "Vox", "Watchdog",
]
__version__ = "1.1.0"
