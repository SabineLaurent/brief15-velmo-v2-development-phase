"""Inspect and erase what the agent remembers about a customer (R5 + R6).

Two requirements of the memory spec live here, and they live together on purpose:

    R6 (traçabilité)  ->  "show me everything you kept about this user"
    R5 (droit à l'oubli) -> "delete this, and prove it is gone"

R5's hard word is **vérifiable**. A delete call that returns `None` proves
nothing: it cannot distinguish "removed three facts" from "matched nothing and
did nothing". So every function here **returns the records it acted on**, and the
erasure path RE-READS each key afterwards to confirm the row is actually gone
(`_verify_absent`). That read-back is what turns "we called delete" into
"the data is no longer there" — and it is cheap, because we only ever re-read
keys we just deleted.

Three deliberate choices worth knowing before changing anything:

**1. Deleting on a weak match is worse than not deleting.**
Recall (`memory_tools.search_memories`) can afford a bad match: a mildly
irrelevant fact in the prompt costs tokens. Deletion cannot — it destroys the
WRONG fact, irreversibly, on a customer's behalf. So `forget_user_memories`
takes a REQUIRED `min_score` (no default: a permissive default on a destructive
operation is a trap) and reports "nothing matched" rather than deleting the
closest thing it found. The value is calibrated per embeddings model — see
`forget_min_score` in `config.py`, which carries the measurement.

**2. The transcript is NOT erasable by `user_id` here — and that is stated, not
hidden.** Long-term memory is keyed by `user_id`, but the conversation
checkpoints are keyed by `thread_id`, and this project keeps no user->threads
index. `forget_thread` therefore takes a `thread_id` a caller already knows.
Erasing every transcript of a user is a real gap; see docs/memoire.md.

**3. Nothing here calls an LLM or an embeddings model, except the one semantic
search a targeted forget needs.** An audit surface that needed a working provider
to answer "what do you know about me?" would be unusable exactly when you need it
most — during an incident, or in CI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from support_agent.memory.long_term import memories_namespace

logger = logging.getLogger(__name__)

# How many items one `search` page asks for. The store's own default is 10, which
# would make a full audit dump lie by omission on any real customer.
_PAGE_SIZE = 100

# Backstop on a full listing: a pathological namespace must not turn an audit
# command into an unbounded scan. Reaching it is logged, never silently ignored —
# a truncated dump that claims to be complete is a worse answer than an error.
_MAX_ITEMS = 5_000



@dataclass(frozen=True)
class MemoryRecord:
    """One stored fact, with the metadata an auditor needs.

    `created_at` / `updated_at` come straight from the store's `Item` — we do not
    maintain our own write journal, because the store already timestamps every
    row. That is what answers "traçabilité des écritures mémoire": when a fact
    was learned, and when it was last touched.
    """

    key: str
    text: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    score: float | None = None

    def __str__(self) -> str:
        stamp = self.created_at.isoformat(timespec="seconds") if self.created_at else "?"
        suffix = f"  (score={self.score:.3f})" if self.score is not None else ""
        return f"[{stamp}] {self.key}  {self.text}{suffix}"


def _to_record(item) -> MemoryRecord:
    """Adapt a store `Item`/`SearchItem` into our own record type.

    Tolerant by design: a row whose `text` went missing (hand-edited database, a
    schema change from an older phase) must still be LISTED and DELETABLE. An
    audit tool that hides malformed rows hides exactly the rows worth seeing.
    """
    value = item.value if isinstance(item.value, dict) else {}
    text = value.get("text")
    return MemoryRecord(
        key=item.key,
        text=text if isinstance(text, str) else repr(value),
        created_at=getattr(item, "created_at", None),
        updated_at=getattr(item, "updated_at", None),
        score=getattr(item, "score", None),
    )


# --- R6: inspect ------------------------------------------------------------


def list_user_memories(store: BaseStore, user_id: str) -> list[MemoryRecord]:
    """Return EVERY fact the agent kept about this user, oldest first.

    Pages through the namespace instead of taking the store's default page: the
    whole point of R6 is completeness, and a dump that stops at ten items would
    answer the question wrongly while looking right.
    """
    namespace = memories_namespace(user_id)
    records: list[MemoryRecord] = []
    offset = 0
    while offset < _MAX_ITEMS:
        # No `query`: we are LISTING a namespace, not searching it. A semantic
        # ranking would reorder an audit dump by relevance to nothing.
        page = store.search(namespace, limit=_PAGE_SIZE, offset=offset)
        records.extend(_to_record(item) for item in page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    else:
        logger.warning(
            "Audit dump for user=%s hit the %d-item cap; the listing is TRUNCATED.",
            user_id,
            _MAX_ITEMS,
        )
    # `created_at` can be None on a store that does not timestamp; sort those last
    # rather than raising in the middle of an audit.
    return sorted(records, key=lambda r: (r.created_at is None, r.created_at))


def search_user_memories(
    store: BaseStore, user_id: str, query: str, *, limit: int = 5
) -> list[MemoryRecord]:
    """Find this user's facts that resemble `query`, best match first."""
    if not query.strip():
        return []
    items = store.search(memories_namespace(user_id), query=query, limit=limit)
    return [_to_record(item) for item in items]


# --- R5: forget -------------------------------------------------------------


def _verify_absent(store: BaseStore, user_id: str, keys: list[str]) -> list[str]:
    """Re-read the keys we just deleted; return those that are STILL present.

    This is the "vérifiable" half of R5. It exists because `delete` returns
    nothing and cannot fail loudly: a backend that swallowed the write, a stale
    cache, or a namespace typo would all look like success.
    """
    namespace = memories_namespace(user_id)
    survivors: list[str] = []
    for key in keys:
        try:
            if store.get(namespace, key) is not None:
                survivors.append(key)
        except Exception:
            # Treat an unreadable key as a survivor: the honest answer to
            # "is it gone?" when we cannot look is "we could not confirm".
            logger.exception("Could not verify deletion of key=%s", key)
            survivors.append(key)
    return survivors


def delete_user_memories(
    store: BaseStore, user_id: str, keys: list[str]
) -> list[str]:
    """Delete these keys for this user; return the keys confirmed gone.

    Raises `RuntimeError` if a row survives its deletion. Failing loudly is the
    point: a GDPR erasure that half-worked must not be reported as done.
    """
    namespace = memories_namespace(user_id)
    for key in keys:
        store.delete(namespace, key)

    survivors = _verify_absent(store, user_id, keys)
    if survivors:
        raise RuntimeError(
            f"Deletion could not be confirmed for user={user_id!r}: "
            f"{len(survivors)} of {len(keys)} keys still readable ({survivors})."
        )
    if keys:
        logger.info("Deleted %d memories for user=%s (verified).", len(keys), user_id)
    return keys


def forget_user_memories(
    store: BaseStore,
    user_id: str,
    query: str,
    *,
    min_score: float,
    limit: int = 5,
) -> list[MemoryRecord]:
    """Forget the facts matching a natural-language request ("my order number").

    Returns the records actually deleted — empty when nothing cleared
    `min_score`. That empty list is a real answer, not a failure: it means the
    agent holds nothing close enough to what was asked, and guessing would delete
    the wrong fact.

    `min_score` is required on purpose. A default here would be a number chosen
    once, for one embeddings model, silently governing a destructive operation
    forever after — see `forget_min_score` in `config.py`.
    """
    matches = [
        record
        for record in search_user_memories(store, user_id, query, limit=limit)
        # `score is None` on a store with no vector index: there is no similarity
        # to judge, so we must NOT delete on a guess.
        if record.score is not None and record.score >= min_score
    ]
    if not matches:
        return []
    delete_user_memories(store, user_id, [record.key for record in matches])
    return matches


def erase_user_memories(store: BaseStore, user_id: str) -> list[MemoryRecord]:
    """Erase EVERYTHING the agent remembers about a user (GDPR art. 17).

    Distinct from the TTL sweeper in `long_term.py`, which is a *retention*
    policy: it expires rows after a delay counted from the LAST ACCESS, so an
    active customer's data never expires. Retention answers "we do not keep data
    forever"; this answers "delete mine, now". Both are needed and neither
    replaces the other.
    """
    records = list_user_memories(store, user_id)
    delete_user_memories(store, user_id, [record.key for record in records])
    return records


def forget_thread(checkpointer: BaseCheckpointSaver, thread_id: str) -> bool:
    """Delete one conversation's checkpoints (the verbatim transcript).

    Separate from the functions above because it is keyed differently —
    `thread_id`, not `user_id` — and that difference is the gap documented in
    point 2 of the module docstring: we can erase a transcript we can NAME, not
    every transcript of a given user.

    Returns False when the backend does not implement `delete_thread`, so a
    caller can report "not erased" instead of assuming success.
    """
    deleter = getattr(checkpointer, "delete_thread", None)
    if deleter is None:
        logger.warning(
            "Checkpointer %s cannot delete threads; transcript kept.",
            type(checkpointer).__name__,
        )
        return False
    deleter(thread_id)
    logger.info("Deleted checkpoints for thread=%s", thread_id)
    return True
