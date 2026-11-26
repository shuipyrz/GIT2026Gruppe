"""Refresh a stale development runtime before importing its public symbols."""
import importlib
import sys
from threading import RLock


BOOTSTRAP_VERSION = 3
SESSION_SCHEMA_VERSION = 2
MIN_AGENT_RUNTIME_VERSION = 21
_lock = globals().get("_lock") or RLock()

# Dependencies precede their consumers so class/exception imports stay coherent.
# Model configuration is deliberately retained in agent.llm.
_DEPENDENCY_ORDER = (
    "core.contracts", "core.tasks", "core.diagnostics", "core.session",
    "gis.data_io", "gis.geometry", "gis.statistics",
    "maps.renderer", "maps.png_export", "maps.interaction",
    "agent.attributes", "agent.intent", "agent.language", "agent.tool_results",
    "agent.tools", "agent.execution", "agent.graph",
)


def refresh_cached_runtime():
    """Reload outdated project modules once, leaving session objects untouched.

    A Streamlit rerun reexecutes frontend.py, but may retain older modules in
    sys.modules. Checking only the session instance cannot repair a missing
    method on the *imported class*. Fresh processes need no explicit reload.
    """
    with _lock:
        session_module = sys.modules.get("core.session")
        graph_module = sys.modules.get("agent.graph")
        stale_session = session_module is not None and (
            getattr(session_module, "SESSION_SCHEMA_VERSION", 0) < SESSION_SCHEMA_VERSION
            or not callable(getattr(getattr(session_module, "SessionData", None), "ensure_compatible", None)))
        stale_graph = graph_module is not None and getattr(graph_module, "AGENT_RUNTIME_VERSION", 0) < MIN_AGENT_RUNTIME_VERSION
        if not (stale_session or stale_graph):
            return False
        importlib.invalidate_caches()
        for name in _DEPENDENCY_ORDER:
            module = sys.modules.get(name)
            if module is not None:
                importlib.reload(module)
        return True
