"""The operator's door onto agent memory: inspect (R6) and erase (R5).

    make memory ARGS='--user-id alice'                      # audit dump
    make memory ARGS='--user-id alice --forget "order id"'   # targeted, dry run
    make memory ARGS='--user-id alice --erase --write'       # GDPR art. 17

Read-only by default, for the same reason `consolidate` is: the first thing you
want from an erasure tool is to see what it is ABOUT to erase. `--write` is what
makes it act.

**Why this is a separate module from `privacy.py`.** The logic lives in
`privacy.py`, which the agent's `forget_memory` tool imports — so `privacy` is
loaded as part of the package on every run. A module that is BOTH imported by the
package and executed with `python -m` gets initialised twice, and Python warns
about it. Keeping the `__main__` in a leaf nobody imports avoids that entirely.
"""

from __future__ import annotations

import argparse
import logging

from support_agent.config import get_settings
from support_agent.memory.long_term import get_store
from support_agent.memory.privacy import (
    MemoryRecord,
    delete_user_memories,
    forget_thread,
    list_user_memories,
    search_user_memories,
)


def main() -> None:
    """CLI entry point (`make memory`)."""
    parser = argparse.ArgumentParser(description="Inspect or erase agent memory.")
    parser.add_argument("--user-id", required=True, help="the customer to inspect")
    parser.add_argument("--forget", metavar="QUERY", help="forget facts matching QUERY")
    parser.add_argument(
        "--erase", action="store_true", help="erase EVERYTHING for this user (art. 17)"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="actually delete (default: show what would be deleted)",
    )
    parser.add_argument("--thread-id", help="also delete this conversation's transcript")
    args = parser.parse_args()

    if args.forget and args.erase:
        parser.error("--forget and --erase are mutually exclusive.")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    store = get_store(settings)
    user_id: str = args.user_id

    # R6 first, and unconditionally: you cannot judge a deletion you have not seen.
    records = list_user_memories(store, user_id)
    print(f"\n=== {len(records)} memories stored for user={user_id!r} ===")
    for record in records:
        print(f"  {record}")

    if not (args.forget or args.erase or args.thread_id):
        return

    targets: list[MemoryRecord] = []
    if args.forget:
        targets = [
            record
            for record in search_user_memories(store, user_id, args.forget)
            if record.score is not None and record.score >= settings.forget_min_score
        ]
        print(
            f"\n=== {len(targets)} matched for deletion "
            f"(floor={settings.forget_min_score}) ==="
        )
    elif args.erase:
        targets = records
        print(f"\n=== {len(targets)} matched for deletion (FULL ERASURE) ===")

    for record in targets:
        print(f"  {record}")

    if args.forget or args.erase:
        if not targets:
            # Said plainly, because "0 deleted" and "deletion failed" must never
            # look the same to whoever runs this during a GDPR request.
            print("Nothing matched — nothing deleted.")
        elif not args.write:
            print("\n[DRY RUN] re-run with --write to delete the above.")
        else:
            deleted = delete_user_memories(store, user_id, [r.key for r in targets])
            print(f"\n[WRITE] deleted and VERIFIED absent: {len(deleted)} memories.")

    if args.thread_id:
        if not args.write:
            print(f"\n[DRY RUN] would delete transcript thread={args.thread_id}")
        else:
            from support_agent.memory.short_term import get_checkpointer

            erased = forget_thread(get_checkpointer(settings), args.thread_id)
            state = "deleted" if erased else "NOT deleted (backend cannot)"
            print(f"\n[WRITE] transcript thread={args.thread_id}: {state}")


if __name__ == "__main__":
    main()
