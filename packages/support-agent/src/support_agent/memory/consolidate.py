"""Distill finished conversations into episodes (the offline half).

    a turn        ->  writes a CANDIDATE   (one upsert, no model)   [close_turn]
    this job      ->  writes an EPISODE    (one LLM call per thread)
    a later turn  ->  reads episodes       (one embedding call)     [support node]

    uv run python -m support_agent.memory.consolidate          # dry run + report
    uv run python -m support_agent.memory.consolidate --write  # actually distil

A separate process rather than an in-process background task: an instance can be
recycled or scaled out at any moment, and an in-RAM queue of pending reflections dies
with it. Candidate rows live in the durable store, so the work survives a restart and
can be re-run, inspected and tested offline.

It reads the checkpointer instead of storing transcripts: the conversation is already
persisted by `thread_id`, and copying it would duplicate personal data into a second
place with its own retention rules. A distilled candidate is deleted, so a re-run cannot
produce the same episode twice.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.base import BaseStore

from support_agent.config import Settings, get_settings
from support_agent.graph import build_support_graph
from support_agent.guardrails import build_tool_guard
from support_agent.llm import get_chat_model
from support_agent.memory.episodic import (
    EPISODE_EXTRACTION_PROMPT,
    Candidate,
    Episode,
    drop_candidate,
    list_ripe_candidates,
    save_episode,
)

logger = logging.getLogger(__name__)

_MIN_EXCHANGES = 2

_MAX_TRANSCRIPT_MESSAGES = 40


@dataclass
class Report:
    """What a run did — printed, and asserted on in tests."""

    examined: int = 0
    distilled: int = 0
    skipped_unresolved: int = 0
    skipped_too_short: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"examined={self.examined} distilled={self.distilled} "
            f"skipped_unresolved={self.skipped_unresolved} "
            f"skipped_too_short={self.skipped_too_short} failed={self.failed}"
        )


def _transcript(graph, thread_id: str) -> list[str] | None:
    """Read a thread back from the checkpointer as plain "role: text" lines.

    Returns None when the thread cannot be read — a checkpoint may have been
    swept by a retention policy while its candidate row survived, and that is a
    normal outcome, not an error.
    """
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        logger.exception("Could not read thread %s; skipping.", thread_id)
        return None

    messages = (snapshot.values or {}).get("messages") or []
    lines: list[str] = []
    for message in messages[-_MAX_TRANSCRIPT_MESSAGES:]:
        if isinstance(message, HumanMessage):
            lines.append(f"customer: {message.text}")
        elif isinstance(message, AIMessage):
            if message.text:
                lines.append(f"agent: {message.text}")
    return lines


def _distill(model, transcript: list[str]) -> Episode:
    """Ask the model for ONE episode. Structured output, so no parsing."""
    extractor = model.with_structured_output(Episode)
    return extractor.invoke(
        [
            {"role": "system", "content": EPISODE_EXTRACTION_PROMPT},
            {"role": "user", "content": "\n".join(transcript)},
        ]
    )


def consolidate(
    *, write: bool, settings: Settings | None = None, graph=None, store: BaseStore | None = None
) -> Report:
    """Distil every ripe candidate into an episode. Returns what it did.

    Args:
        write: False performs a DRY RUN — it lists what would be distilled and
            spends no LLM call. The default on purpose: the first thing you want
            from a learning loop is to see what it is about to learn.
        settings/graph/store: injectable for tests; built from config otherwise.
    """
    settings = settings or get_settings()
    if graph is None:
        graph = build_support_graph()
    if store is None:
        store = graph.store
    report = Report()

    tool_guard = (
        build_tool_guard(
            settings.guardrails_max_tool_field_chars,
            settings.guardrails_action_rate_limit,
            settings.guardrails_action_rate_window_s,
        )
        if settings.guardrails_enabled
        else None
    )
    model = get_chat_model(settings) if write else None

    candidates: list[Candidate] = list_ripe_candidates(
        store, idle_minutes=settings.episodic_idle_minutes
    )
    for candidate in candidates:
        report.examined += 1

        if not candidate.resolved:
            report.skipped_unresolved += 1
            if write:
                drop_candidate(store, candidate.thread_id)
            continue

        transcript = _transcript(graph, candidate.thread_id)
        if transcript is None or len(transcript) < _MIN_EXCHANGES:
            report.skipped_too_short += 1
            if write:
                drop_candidate(store, candidate.thread_id)
            continue

        if not write:
            logger.info(
                "[dry run] would distil thread=%s (%d lines)",
                candidate.thread_id,
                len(transcript),
            )
            report.distilled += 1
            continue

        try:
            episode = _distill(model, transcript)
        except Exception:
            logger.exception("Distillation failed for thread=%s.", candidate.thread_id)
            report.failed += 1
            continue

        key = save_episode(store, episode, tool_guard=tool_guard)
        drop_candidate(store, candidate.thread_id)
        report.distilled += 1
        logger.info("Distilled thread=%s into episode=%s", candidate.thread_id, key)

    return report


def main() -> None:
    """CLI entry point (`make consolidate`)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write",
        action="store_true",
        help="actually call the LLM and store episodes (default: dry run)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    report = consolidate(write=args.write)
    mode = "WRITE" if args.write else "DRY RUN"
    print(f"[{mode}] {report}")


if __name__ == "__main__":
    main()
