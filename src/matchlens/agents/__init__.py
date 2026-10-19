"""Agent layer: specialised agents that explain, verify, personalise and recap detected moments.

The deterministic pipeline (stats, moments, fast-path cues) never waits on this
layer. Agents enrich moments asynchronously and their output replaces the
templated cues when it arrives in time and passes the fact-checker.
"""

from .config import FoundrySettings, make_chat_client
from .engine import AgentEngine, EngineMetrics

__all__ = ["AgentEngine", "EngineMetrics", "FoundrySettings", "make_chat_client"]
