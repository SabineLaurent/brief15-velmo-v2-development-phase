"""Unit tests for the FAQ index fingerprint — no network, no vector store.

The contract has two halves, and both matter:

- it must CHANGE whenever the stored vectors would become wrong (otherwise the
  agent silently answers from a stale FAQ);
- it must NOT change otherwise (otherwise we rebuild on every startup and the
  whole point of persisting the index is lost).
"""

from __future__ import annotations

from pathlib import Path

from support_agent.knowledge.fingerprint import (
    compute_fingerprint,
    read_fingerprint,
    write_fingerprint,
)

BASE = {
    "embeddings_provider": "mistral",
    "embeddings_model": "mistral-embed",
    "chunk_size": 800,
    "chunk_overlap": 120,
}


def _faq_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    faq = tmp_path / "faq"
    faq.mkdir(parents=True)
    for name, text in files.items():
        (faq / name).write_text(text, encoding="utf-8")
    return faq


def test_stable_across_identical_calls(tmp_path: Path) -> None:
    faq = _faq_dir(tmp_path, {"a.md": "délais de livraison", "b.md": "retours"})
    assert compute_fingerprint(faq, **BASE) == compute_fingerprint(faq, **BASE)


def test_changes_when_a_file_content_changes(tmp_path: Path) -> None:
    faq = _faq_dir(tmp_path, {"a.md": "livraison en 2 jours"})
    before = compute_fingerprint(faq, **BASE)
    (faq / "a.md").write_text("livraison en 5 jours", encoding="utf-8")
    assert compute_fingerprint(faq, **BASE) != before


def test_changes_when_a_file_is_added_or_removed(tmp_path: Path) -> None:
    faq = _faq_dir(tmp_path, {"a.md": "livraison"})
    before = compute_fingerprint(faq, **BASE)

    (faq / "b.md").write_text("retours", encoding="utf-8")
    with_two = compute_fingerprint(faq, **BASE)
    assert with_two != before

    (faq / "b.md").unlink()
    assert compute_fingerprint(faq, **BASE) == before


def test_changes_when_a_file_is_renamed(tmp_path: Path) -> None:
    """Renaming matters: the file name is the `source` used in citations."""
    faq = _faq_dir(tmp_path, {"livraison.md": "délais"})
    before = compute_fingerprint(faq, **BASE)
    (faq / "livraison.md").rename(faq / "shipping.md")
    assert compute_fingerprint(faq, **BASE) != before


def test_changes_when_the_embedding_model_changes(tmp_path: Path) -> None:
    """Different model = different vector space: old vectors become meaningless."""
    faq = _faq_dir(tmp_path, {"a.md": "livraison"})
    other = {**BASE, "embeddings_model": "text-embedding-3-small"}
    assert compute_fingerprint(faq, **other) != compute_fingerprint(faq, **BASE)


def test_changes_when_chunking_changes(tmp_path: Path) -> None:
    """The trap that is easy to forget: same files, different cuts."""
    faq = _faq_dir(tmp_path, {"a.md": "livraison"})
    reference = compute_fingerprint(faq, **BASE)
    assert compute_fingerprint(faq, **{**BASE, "chunk_size": 600}) != reference
    assert compute_fingerprint(faq, **{**BASE, "chunk_overlap": 0}) != reference


def test_no_collision_between_name_and_content(tmp_path: Path) -> None:
    """Hashed parts are separated, so ("ab", "c") cannot collide with ("a", "bc")."""
    left = _faq_dir(tmp_path / "l", {"ab.md": "c"})
    right = _faq_dir(tmp_path / "r", {"a.md": "bc"})
    assert compute_fingerprint(left, **BASE) != compute_fingerprint(right, **BASE)


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "meta.json"  # parent dirs created on write
    write_fingerprint(path, "abc123", context={"embeddings_model": "mistral-embed"})
    assert read_fingerprint(path) == "abc123"


def test_read_returns_none_when_unusable(tmp_path: Path) -> None:
    """Any unreadable state means "cannot prove validity" -> rebuild, never crash."""
    assert read_fingerprint(tmp_path / "missing.json") is None

    corrupted = tmp_path / "corrupted.json"
    corrupted.write_text("{not json", encoding="utf-8")
    assert read_fingerprint(corrupted) is None

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text('{"other": "key"}', encoding="utf-8")
    assert read_fingerprint(incomplete) is None
