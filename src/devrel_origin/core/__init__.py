"""
DevTools Advocate Agent System

A multi-agent system for autonomous developer advocacy,
built on Claude Agent SDK and Model Context Protocol (MCP).
"""

from devrel_origin.core.argus import (
    Argus,
    PerformanceMetric,
    PerformanceReport,
    Recommendation,
)
from devrel_origin.core.atlas import Atlas
from devrel_origin.core.cyra import (
    CroReport,
    Cyra,
    DropOff,
    FunnelStep,
    Hypothesis,
)
from devrel_origin.core.dex import Dex
from devrel_origin.core.echo import Echo
from devrel_origin.core.iris import Iris
from devrel_origin.core.kai import Kai
from devrel_origin.core.mox import Mox
from devrel_origin.core.nova import Nova
from devrel_origin.core.pax import Pax
from devrel_origin.core.rex import Rex
from devrel_origin.core.sage import Sage
from devrel_origin.core.sentinel import Sentinel
from devrel_origin.core.vox import Vox
from devrel_origin.core.watchdog import Watchdog

__all__ = [
    "Argus",
    "Atlas",
    "Cyra",
    "Dex",
    "Echo",
    "Kai",
    "Mox",
    "Sage",
    "Iris",
    "Nova",
    "Pax",
    "Rex",
    "Sentinel",
    "Vox",
    "Watchdog",
    "CroReport",
    "DropOff",
    "FunnelStep",
    "Hypothesis",
    "PerformanceMetric",
    "PerformanceReport",
    "Recommendation",
]
