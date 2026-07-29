"""Prompt-injection detection (Phase 12-A, input guardrails).

Same agnostic shape as PII: `InjectionDetector` is a **port**, the baseline is a
list of known jailbreak / instruction-override phrasings (English + French, the
demo's languages). This is a heuristic FIRST LINE, not a guarantee: pattern lists
have false positives and negatives. A real classifier (an LLM judge, a dedicated
model) plugs in behind the same port when the baseline is not enough.
"""

from __future__ import annotations

import re
from typing import Protocol


class InjectionDetector(Protocol):
    """Port: decide whether a piece of text looks like a prompt-injection attempt."""

    def scan(self, text: str) -> bool: ...  # True = suspicious


# Known injection / jailbreak phrasings. Compiled case-insensitively. Kept broad
# but simple; the point is to catch the obvious attempts, not to be exhaustive.
_DEFAULT_INJECTION_PATTERNS: tuple[str, ...] = (
    # English
    r"ignore (?:all |the |your )?(?:previous |above |prior )?(?:instructions|rules)",
    r"disregard (?:all |the |your )?(?:previous )?(?:instructions|rules)",
    r"forget (?:all |everything|your )?(?:previous )?(?:instructions|rules)",
    r"(?:reveal|show|print|repeat|display|tell me)\b.{0,20}\b(?:system )?(?:prompt|instructions)",
    r"you are now\b",
    r"pretend (?:to be|you are)\b",
    r"act as (?:if|an?|a)\b",
    r"developer mode",
    r"jailbreak",
    r"\bDAN\b",
    r"new instructions\s*:",
    r"override (?:your )?(?:instructions|rules|safety|guardrails)",
    # French (demo language)
    r"ignore[sz]?\b.{0,20}\b(?:instructions|consignes|r[eè]gles)",
    r"oublie[sz]?\b.{0,20}\b(?:instructions|consignes|r[eè]gles)",
    r"(?:montre|affiche|r[ée]v[èe]le|donne)[sz]?(?:-moi)?\b.{0,20}\b(?:syst[èe]me\s+)?prompt",
    r"tu es maintenant\b",
    r"fais comme si\b",
    r"mode d[ée]veloppeur",
    # Secret / configuration EXFILTRATION. Same family as the above (make the
    # agent reveal what it was not built to reveal) and the same response, so it
    # lives behind the same port rather than in a fourth detector. `secrets.py`
    # is the OUTPUT side of this pair: it catches a key on the way out, this
    # catches the request on the way in — defence in depth, both directions.
    r"(?:donne|montre|affiche|r[ée]v[èe]le|liste)[sz]?(?:-moi)?\b.{0,40}\b"
    r"(?:cl[ée]s?\s+api|api[_\s-]?keys?|token|mots?\s+de\s+passe|password|"
    r"variables?\s+d['’]environnement|env\s+vars?|secrets?|credentials?)",
    r"(?:quel(?:le)?\s+est|c['’]est\s+quoi)\b.{0,30}\b"
    r"(?:ta\s+cl[ée]|ton\s+token|le\s+secret|le\s+mot\s+de\s+passe)",
    r"secret\s+de\s+configuration",
    r"(?:cl[ée]|token|password|mot\s+de\s+passe)\s+de\s+(?:la\s+)?base(?:\s+de\s+donn[ée]es)?",
)


class RegexInjectionDetector:
    """Baseline `InjectionDetector`: matches against a list of known patterns."""

    def __init__(self, patterns: tuple[str, ...] = _DEFAULT_INJECTION_PATTERNS) -> None:
        self._patterns = [re.compile(p, re.IGNORECASE) for p in patterns]

    def scan(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self._patterns)
