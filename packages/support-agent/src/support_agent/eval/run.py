"""Run the evaluation on LangSmith.

    target(inputs)  ->  invoke the real graph, return {route, answer, tool_output}
    client.evaluate ->  run the target over the dataset, apply every evaluator,
                        and log a comparable experiment to LangSmith.

The target is provider-agnostic: it drives the same `build_support_graph()` the app
uses, so changing `.env` and re-running produces a new experiment you can diff against
the previous one — which is exactly how a quality regression shows up when switching
LLMs.

    make eval
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
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        graph.invoke(
            {"messages": [{"role": "user", "content": inputs["message"]}]},
            context=AgentContext(user_id=user_id),
            config=config,
        )
        state = graph.get_state(config)

        route = state.values.get("route")
        messages = state.values.get("messages", [])
        answer = next(
            (m.text for m in reversed(messages) if isinstance(m, AIMessage) and m.text),
            "",
        )
        tool_output = "\n".join(m.text for m in messages if isinstance(m, ToolMessage))
        return {
            "route": route,
            "answer": answer,
            "tool_output": tool_output,
            "usage": _token_usage(messages),
        }

    return target


def _token_usage(messages: list) -> dict[str, int]:
    """Sum the token usage carried by the messages PERSISTED IN STATE.

    A FLOOR, not the true total, and the report says so: it counts only LLM calls whose
    reply was appended to the graph state, so a node that calls the model without adding
    a message (the router, a tool-choice pass) is invisible here. Its job is to make a
    regression in token consumption VISIBLE, not to bill anyone.

    `usage_metadata` is the provider-agnostic shape LangChain normalises into, so no
    provider branch is needed. Absent means the provider did not report usage — counted
    as zero rather than crashing a report.
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

    push_dataset(client)

    graph = build_support_graph(learn_from_turns=False)
    target = make_target(graph)

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
