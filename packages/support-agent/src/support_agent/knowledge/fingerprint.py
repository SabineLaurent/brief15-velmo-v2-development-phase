"""Is the stored FAQ index still a faithful projection of the source?

A persistent index is a *projection* of the FAQ files. If the source changes and
the projection does not, the agent keeps answering from a stale FAQ **without
raising anything** — the worst kind of bug, because it looks like it works.

So we store a fingerprint next to the index and compare it at startup. It covers
everything that would make the stored vectors wrong:

- **what** we index: the name and content of every FAQ file;
- **how** we index it: the embedding provider and model (different model =
  different vector space, so old vectors become meaningless), and the chunking
  parameters (different cuts = different chunks).

Not to be confused with memoization (see `docs/glossaire.md`): memoization asks
"do I already know the answer?" and assumes it never changes; a fingerprint asks
"is what I stored still valid?" and assumes the source may have moved. The first
buys speed, the second protects correctness.

This module deliberately knows nothing about Chroma, LangChain or the agent: it
hashes bytes and reads/writes one small JSON file. That keeps it trivially
testable — no network, no vector store.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Separator between hashed parts. Without it, ("ab", "c") and ("a", "bc") would
# hash identically — a collision that would silently accept a stale index.
_SEP = b"\x00"


def compute_fingerprint(
    knowledge_dir: Path,
    *,
    embeddings_provider: str,
    embeddings_model: str,
    chunk_size: int,
    chunk_overlap: int,
) -> str:
    """Return a hash of everything the stored vectors depend on.

    Args:
        knowledge_dir: Directory holding the FAQ `*.md` files.
        embeddings_provider: Provider name, e.g. `"mistral"`.
        embeddings_model: Model name, e.g. `"mistral-embed"`.
        chunk_size: Character size used to split documents.
        chunk_overlap: Character overlap between consecutive chunks.
    """
    hasher = hashlib.sha256()

    # WHAT we index. Sorted so the fingerprint does not depend on filesystem
    # ordering; the name is hashed too, so renaming a file invalidates the index
    # (`source` metadata feeds the agent's citations).
    for path in sorted(knowledge_dir.glob("*.md")):
        hasher.update(path.name.encode("utf-8"))
        hasher.update(_SEP)
        hasher.update(path.read_bytes())
        hasher.update(_SEP)

    # HOW we index it.
    for part in (embeddings_provider, embeddings_model, str(chunk_size), str(chunk_overlap)):
        hasher.update(part.encode("utf-8"))
        hasher.update(_SEP)

    return hasher.hexdigest()


def read_fingerprint(path: Path) -> str | None:
    """Return the stored fingerprint, or `None` if it cannot be read.

    Any failure — missing file, unreadable, corrupted JSON, missing key — means
    "we cannot prove the index is valid", which is treated exactly like a
    mismatch: rebuild. A cache must never fail the caller.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))["fingerprint"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_fingerprint(path: Path, fingerprint: str, *, context: dict[str, str]) -> None:
    """Store `fingerprint`, alongside human-readable context for debugging.

    Only `fingerprint` is ever read back; `context` exists so that opening the
    file answers "why did it rebuild?" without re-running anything.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": fingerprint, **context}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
