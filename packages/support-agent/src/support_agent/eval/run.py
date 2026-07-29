"""Run the evaluation on LangSmith (Phase 9).

    target(inputs)  ->  invoke the real graph, return {route, answer, tool_output}
    client.evaluate ->  run the target over the dataset, apply every evaluator,
                        and log a comparable experiment to LangSmith.

The target is provider-agnostic: it drives the same `build_support_graph()` the
app uses, so changing `.env` (provider/model) and re-running produces a new
experiment you can diff against the previous one in the LangSmith UI — that is
exactly how you catch a quality regression when switching LLMs.

Run with:

    make eval            # or: uv run python -m support_agent.eval.run
"""

from __future__ import annotations

import functools
import uuid
from typing import Callable

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from support_agent.config import get_settings
from support_agent.eval.dataset import DATASET_NAME, push_dataset
from support_agent.eval.evaluators import ALL_EVALUATORS, Evaluator
from support_agent.graph import build_support_graph
from support_agent.memory import AgentContext


def _for_langsmith(evaluator: Evaluator) -> Evaluator:
    """Adapt a pure evaluator to LangSmith's runner.

    Our evaluators return `None` when a metric does not apply to a case — clean
    for the pytest gate. LangSmith's runner, however, rejects any falsy result
    (`None`, `[]`, `{}`). We translate "not applicable" into an empty results set
    (`{"results": []}`), a non-empty dict that records no feedback and no error.
    """

    @functools.wraps(evaluator)
    def wrapped(inputs: dict, outputs: dict, reference_outputs: dict):
        result = evaluator(inputs, outputs, reference_outputs)
        return result if result is not None else {"results": []}

    return wrapped


def make_target(graph: CompiledStateGraph) -> Callable[[dict], dict]:
    """Build the evaluation target: one function that runs the agent on an input.

    Reuses a single compiled graph across cases (cheap) but gives each case a
    fresh `thread_id`, so conversations stay isolated from one another.
    """

    def target(inputs: dict) -> dict:
        user_id = inputs.get("user_id", "demo-user")
        # Fresh thread per case = no short-term-memory bleed between examples.
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        # An `escalate` case pauses on `interrupt()`; `invoke` returns normally
        # with `__interrupt__` (it does not raise). We do NOT resume — we only
        # care that the router chose the right branch. So we read the persisted
        # state as the single source of truth for both route and messages.
        graph.invoke(
            {"messages": [{"role": "user", "content": inputs["message"]}]},
            context=AgentContext(user_id=user_id),
            config=config,
        )
        state = graph.get_state(config)

        route = state.values.get("route")
        messages = state.values.get("messages", [])
        answer = next(
            (m.content for m in reversed(messages) if isinstance(m, AIMessage) and m.content),
            "",
        )
        tool_output = "\n".join(
            m.content for m in messages if isinstance(m, ToolMessage)
        )
        return {
            "route": route,
            "answer": answer,
            "tool_output": tool_output,
            "usage": _token_usage(messages),
        }

    return target


def _token_usage(messages: list) -> dict[str, int]:
    """Sum the token usage carried by the messages PERSISTED IN STATE.

    Feeds the `cout` line of the MLOps report (`eval/mlops.py`).

    ⚠️ This is a FLOOR, not the true total, and the report says so. It counts only
    LLM calls whose reply was appended to the graph state — a node that calls the
    model to make a decision without adding a message (the router, a tool-choice
    pass) is invisible here. The exact figure lives in the LangSmith trace, which
    sees every call; this is the number available without a LangSmith round trip,
    and its job is to make a regression in token consumption VISIBLE, not to bill
    anyone.

    `usage_metadata` is the provider-agnostic shape LangChain normalises into, so
    no provider branch is needed. Absent (or None) means the provider did not
    report usage — counted as zero rather than crashing a report.
    """
    totals = {"input_tokens": 0, "output_tokens": 0}
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        for key in totals:
            totals[key] += usage.get(key) or 0
    return totals


def main() -> None:
    from langsmith import Client

    settings = get_settings()
    client = Client()

    # 1. Make sure the versioned dataset exists in LangSmith.
    push_dataset(client)

    # 2. Build the agent once and wrap it as the evaluation target.
    #    `learn_from_turns=False`: a benchmark must not teach the thing it grades.
    #    With it on, each run would distil episodes out of the very conversations
    #    used to score the agent, and the NEXT run would be graded against a pool
    #    the previous one grew — a score that improves on its own. Recall stays on
    #    (we measure the agent as deployed); only the write side is cut.
    graph = build_support_graph(learn_from_turns=False)
    target = make_target(graph)

    # 3. Score the whole dataset. The experiment name carries the provider/model
    #    so experiments are easy to compare when you swap `.env`.
    prefix = f"{settings.llm_provider}-{settings.llm_model}"
    results = client.evaluate(
        target,
        data=DATASET_NAME,
        evaluators=[_for_langsmith(ev) for ev in ALL_EVALUATORS],
        experiment_prefix=prefix,
        max_concurrency=2,
        metadata={
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "phase": "9-evaluation",
        },
    )
    print(results)


if __name__ == "__main__":
    main()
