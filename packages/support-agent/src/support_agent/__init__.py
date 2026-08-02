"""Agnostic consumer-support AI agent.

`stream_reply` is the public API seam: the one stable entry point a front end calls.
Everything lang* stays hidden behind it.
"""

from support_agent.api import stream_reply

__version__ = "0.1.0"

__all__ = ["stream_reply"]
