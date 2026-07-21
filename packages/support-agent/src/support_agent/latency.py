"""TTFT / per-node latency harness (scope B — see docs/latence.md).

The metric that matters for a chat UI is NOT the total response time but the
**time-to-first-token** (TTFT): the silence before the customer sees ANYTHING.
This harness measures where the wall-clock time goes on a real turn — the TTFT
plus the duration of every graph node — so we can PROVE a latency change instead
of guessing it. The obvious use: compare "everything on the strong model" against
the small->strong cascade (set `LLM_FAST_MODEL`), before vs after.

How it measures (one pass, two stream modes at once):
- `stream_mode=["updates", "messages"]` yields `(mode, chunk)` tuples.
  * "updates" fires when a node COMPLETES -> we time each node from the previous
    super-step boundary (`router`, `model` x2 for the ReAct loop, `tools`, ...).
  * "messages" carries streamed tokens -> the FIRST one from a customer-facing
    node (same filter as the API seam) is the TTFT.

This runs the REAL compiled graph (via `api.get_agent`), so it measures exactly
what a front end would experience. It makes real LLM calls (it costs tokens).

    make latency
    uv run python -m support_agent.latency "Je veux retourner un article"
    uv run python -m support_agent.latency --runs 5
"""

from __future__ import annotations

import argparse
import statistics
import time
import uuid
from dataclasses import dataclass

from support_agent.api import CUSTOMER_FACING_NODES, get_agent
from support_agent.config import get_settings
from support_agent.memory import AgentContext

# The reference question from docs/latence.md: a factual query that takes the
# full support path (router -> tool decision -> FAQ retrieval -> answer), i.e.
# the worst case for TTFT (three sequential LLM hops).
DEFAULT_QUESTION = "Quels sont vos délais de livraison ?"


@dataclass
class NodeTiming:
    """One graph node and how long it took (wall-clock, between super-steps).

    When the node ran an LLM whose response carried usage, we also record the
    input-token count and how many of those were served from the provider's
    prompt cache (`cache_read`) — that is how we PROVE prompt caching bites
    instead of assuming it (see docs/prompt-caching.md). Both are `None` when the
    node exposed no usage (e.g. the router's structured-output call, or a
    provider that does not report usage on a streamed response).
    """

    name: str
    duration_s: float
    input_tokens: int | None = None
    cache_read: int | None = None


@dataclass
class Report:
    """The timing breakdown of a single measured turn."""

    question: str
    nodes: list[NodeTiming]
    ttft_s: float | None  # None if the turn streamed no customer-facing token
    stream_s: float  # from first to last streamed token
    total_s: float
    token_count: int


def _extract_usage(payload: object) -> tuple[int | None, int | None]:
    """Pull (input_tokens, cache_read) from a node's state update, if any.

    An LLM node returns `{"messages": [AIMessage(...)]}`; the message carries
    `usage_metadata` when the provider reports usage. We read `input_tokens` and
    `input_token_details.cache_read` (the LangChain-normalised name for the tokens
    served from the prompt cache — Azure/OpenAI's `cached_tokens`). Returns
    `(None, None)` when the payload holds no message with usage.
    """
    if not isinstance(payload, dict):
        return None, None
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None, None
    for msg in messages:
        usage = getattr(msg, "usage_metadata", None)
        if not usage:
            continue
        input_tokens = usage.get("input_tokens")
        details = usage.get("input_token_details") or {}
        cache_read = details.get("cache_read")
        return input_tokens, cache_read
    return None, None


def measure_once(message: str, *, user_id: str, thread_id: str) -> Report:
    """Run one turn through the real graph and time it node by node + TTFT.

    Uses a FRESH `thread_id` per call so a growing conversation history never
    skews the timing. Assumes the graph is already built (warm) — build it once
    outside the timed section (see `main`), as the first build is expensive
    (embeddings probe, vector store) and unrelated to per-turn latency.
    """
    agent = get_agent()
    context = AgentContext(user_id=user_id)
    config = {
        "configurable": {"thread_id": thread_id},
        "run_name": "latency-probe",
        "tags": ["latency"],
    }
    inputs = {"messages": [{"role": "user", "content": message}]}

    nodes: list[NodeTiming] = []
    ttft_s: float | None = None
    last_token_s = 0.0
    token_count = 0

    start = time.perf_counter()
    prev = start  # the previous super-step boundary (last node completion)
    for mode, chunk in agent.stream(
        inputs, context=context, config=config, stream_mode=["updates", "messages"]
    ):
        now = time.perf_counter()
        if mode == "updates":
            # A node just finished: attribute the time since the last boundary to
            # it. `model` legitimately appears twice (decide, then answer).
            for node_name, payload in chunk.items():
                input_tokens, cache_read = _extract_usage(payload)
                nodes.append(NodeTiming(node_name, now - prev, input_tokens, cache_read))
                prev = now
        elif mode == "messages":
            # Same filter as the API seam: only customer-facing nodes count as a
            # visible token, so the router's internal decision never skews TTFT.
            token, meta = chunk
            if meta.get("langgraph_node") not in CUSTOMER_FACING_NODES:
                continue
            text = getattr(token, "content", "")
            if isinstance(text, str) and text:
                if ttft_s is None:
                    ttft_s = now - start
                token_count += 1
                last_token_s = now - start

    total_s = time.perf_counter() - start
    stream_s = (last_token_s - ttft_s) if ttft_s is not None else 0.0
    return Report(message, nodes, ttft_s, stream_s, total_s, token_count)


def print_report(report: Report, *, run_label: str = "") -> None:
    """Pretty-print one report: the per-node timeline, then the headline metrics."""
    header = f"— {run_label} " if run_label else "— "
    print(f"\n{header}question: {report.question!r}")
    for node in report.nodes:
        bar = "█" * min(40, round(node.duration_s * 10))
        # Show the prompt-cache hit for LLM nodes that reported usage:
        # cache_read / input_tokens (e.g. "cache 1856/2310").
        cache = ""
        if node.input_tokens is not None:
            read = node.cache_read or 0
            cache = f"  cache {read}/{node.input_tokens}"
        print(f"    {node.name:<14} {node.duration_s:6.2f}s  {bar}{cache}")
    ttft = f"{report.ttft_s:.2f}s" if report.ttft_s is not None else "n/a (no stream)"
    print(
        f"  → TTFT {ttft}  |  streaming {report.stream_s:.2f}s  "
        f"|  total {report.total_s:.2f}s  |  {report.token_count} tokens"
    )
    # Verdict: did the prompt cache bite this turn? Sum over LLM nodes that
    # reported usage. This is the whole point of the instrumentation.
    total_input = sum(n.input_tokens for n in report.nodes if n.input_tokens is not None)
    total_cached = sum(n.cache_read for n in report.nodes if n.cache_read is not None)
    if total_input:
        pct = 100 * total_cached / total_input
        verdict = "cache HIT" if total_cached else "cache cold (0 read)"
        print(f"  → prompt cache: {total_cached}/{total_input} input tokens read ({pct:.0f}%) — {verdict}")


def main() -> None:
    """CLI entry point: build the graph once (warm), then measure N turns."""
    parser = argparse.ArgumentParser(
        description="Measure TTFT and per-node latency of the support graph."
    )
    parser.add_argument(
        "question", nargs="?", default=DEFAULT_QUESTION, help="the customer message to time"
    )
    parser.add_argument(
        "--runs", type=int, default=1, help="number of measured runs (fresh thread each)"
    )
    parser.add_argument("--user-id", default="latency-user", help="simulated customer id")
    args = parser.parse_args()

    settings = get_settings()

    # Warm the expensive graph build OUTSIDE the timed section (unrelated to
    # per-turn latency): embeddings probe, vector store, checkpointer/store.
    print("Building graph (warm-up, not timed)…")
    get_agent()

    fast = settings.llm_fast_model or "(none — cascade OFF, everything on strong)"
    print(f"strong model : {settings.llm_provider}:{settings.llm_model}")
    print(f"fast model   : {fast}")

    ttfts: list[float] = []
    for i in range(1, args.runs + 1):
        report = measure_once(
            args.question, user_id=args.user_id, thread_id=str(uuid.uuid4())
        )
        print_report(report, run_label=f"run {i}/{args.runs}")
        if report.ttft_s is not None:
            ttfts.append(report.ttft_s)

    if len(ttfts) > 1:
        print(
            f"\nTTFT over {len(ttfts)} runs — "
            f"median {statistics.median(ttfts):.2f}s  "
            f"min {min(ttfts):.2f}s  max {max(ttfts):.2f}s"
        )


if __name__ == "__main__":
    main()
