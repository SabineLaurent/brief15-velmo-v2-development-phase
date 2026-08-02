"""Content moderation: hate, violence, self-harm, sexual content.

`ContentModerator` is a port; the baseline is a rule list, and a real classifier (a
moderation API, a small local model) plugs in behind the same interface without the
graph changing.

It returns the CATEGORY, not a boolean: the decision has to be logged, and the category
also selects which refusal the customer reads. Self-harm is its own category rather than
a flavour of violence, so someone in distress is pointed somewhere useful instead of
getting a cold refusal.

Text is accent- and apostrophe-folded before matching. A filter that misses "nudite"
because it only knows "nudité" lets an attacker walk through by typing without accents
once.

A pattern list is a FIRST LINE with both false negatives and false positives,
deliberately tuned to prefer a miss over a false positive: wrongly refusing an angry-
but-legitimate customer costs a real client, while a miss degrades to the model's own
refusal behaviour.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol


class ContentModerator(Protocol):
    """Port: classify text as harmful content, or not.

    Returns the offending category (`"hate"`, `"violence"`, `"self_harm"`,
    `"sexual"`) or `None` when nothing matched.
    """

    def scan(self, text: str) -> str | None: ...


def fold(text: str) -> str:
    """Lowercase, strip accents, and normalise apostrophes.

    Accent stripping goes through NFD and drops the combining marks, so "nudité"
    and "nudite" — and "sous-êtres" and "sous-etres" — collapse to one form. The
    patterns below are therefore written WITHOUT accents.
    """
    lowered = text.lower().replace("’", "'").replace("ʼ", "'")
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


_HATE_PATTERNS: tuple[str, ...] = (
    r"sous-\s?(?:etres?|humains?|hommes?)",
    r"(?:sont|etes|est)\s+(?:des\s+|un\s+)?(?:vermines?|parasites?|animaux)\b",
    r"devraient?\s+(?:tous?\s+)?disparaitre",
    r"sale\s+(?:race|negre|arabe|juif|juive|pede|blanc|noir)",
    r"\bnegres?\b",
    r"retourne[zr]?\b.{0,30}\bdans\s+(?:ton|votre|leur)\s+pays",
)

_VIOLENCE_PATTERNS: tuple[str, ...] = (
    r"je\s+vais\s+(?:te|vous|le|la|les|lui)\s+(?:frapper|tuer|buter|defoncer|casser|crever|massacrer|etrangler)",
    r"je\s+vais\s+(?:tuer|buter|defoncer|massacrer|etrangler)\s+(?:ton|ta|tes|votre|vos|le|la|les)\b",
    r"je\s+(?:te|vous)\s+(?:retrouve|attends)\b.{0,30}\b(?:dehors|parking|chez\s+(?:toi|vous))",
    r"c'?est\s+une\s+menace",
    r"(?:je\s+)?(?:te|vous)\s+menace\b",
    r"comment\s+(?:faire\s+du\s+mal|blesser|tuer)\s+(?:a\s+)?(?:quelqu'?un|une?\s+personne)",
)

_SELF_HARM_PATTERNS: tuple[str, ...] = (
    r"me\s+faire\s+du\s+mal",
    r"me\s+(?:suicider|tuer|mutiler)",
    r"(?:envie|pense)\w*\s+(?:de\s+)?(?:mourir|en\s+finir)",
    r"en\s+finir\s+avec\s+(?:la\s+vie|tout)",
    r"passer\s+a\s+l'?acte",
)

_SEXUAL_PATTERNS: tuple[str, ...] = (
    r"contenu\s+sexuel",
    r"sexuellement\s+explicite",
    r"(?:scene|photo|image|video)s?\s+(?:de\s+)?(?:nudite|sexe|porno)",
    r"nudite\s+(?:detaillee|explicite)",
    r"\bporno(?:graphi\w+)?\b",
    r"(?:decris|raconte|ecris)\b.{0,25}\b(?:scene\s+)?(?:de\s+)?sexe\b",
)

_DEFAULT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("self_harm", _SELF_HARM_PATTERNS),
    ("hate", _HATE_PATTERNS),
    ("violence", _VIOLENCE_PATTERNS),
    ("sexual", _SEXUAL_PATTERNS),
)


class RegexContentModerator:
    """Baseline `ContentModerator`: ordered category rules over folded text."""

    def __init__(
        self, rules: tuple[tuple[str, tuple[str, ...]], ...] = _DEFAULT_RULES
    ) -> None:
        self._rules = [
            (category, [re.compile(p) for p in patterns]) for category, patterns in rules
        ]

    def scan(self, text: str) -> str | None:
        folded = fold(text)
        for category, patterns in self._rules:
            if any(pattern.search(folded) for pattern in patterns):
                return category
        return None
