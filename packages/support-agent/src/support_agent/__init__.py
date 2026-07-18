"""Agnostic consumer-support AI agent.

Built step by step with LangChain, LangGraph and LangSmith.
See ROADMAP.md for the learning path.

`stream_reply` is the public API seam (Phase B1.1): the ONE stable entry point a
front end calls. Everything lang* stays hidden behind it.
"""

from support_agent.api import stream_reply

__version__ = "0.1.0"

__all__ = ["stream_reply"]
