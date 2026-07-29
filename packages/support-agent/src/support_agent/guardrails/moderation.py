"""Content moderation (chantier 2): hate, violence, self-harm, sexual content.

Same agnostic shape as the other detectors — `ContentModerator` is a **port**, the
baseline is a rule list, and a real classifier (a moderation API, a small local
model) plugs in behind the same interface without the graph changing. See the
ROADMAP's "durcissement futur des détecteurs".

Three things make this different from `injection.py`, and each is deliberate:

1. **It returns the CATEGORY, not a boolean.** The brief asks for the decision to
   be journalisée, and "blocked" alone is not a log worth keeping. The category
   also selects which refusal the customer reads.

2. **Self-harm is its own category, not a flavour of violence.** Lumping them
   would answer someone in distress with a cold "I cannot help with that", which
   is the wrong thing to say to that person. It gets a message that points
   somewhere useful instead. That costs one enum value and it is worth it.

3. **Text is accent- and apostrophe-folded before matching.** Not cosmetic: this
   repo has already been bitten by exactly this, in `eval/evaluators.py`, where
   `honest_refusal` scored 0 or 1 depending on whether the model emitted U+2019
   or ASCII `'` (commit f404775). A moderation filter that misses "nudite"
   because it only knows "nudité" is the same bug with a much worse blast radius:
   an attacker types without accents once and walks through.

⚠️ **What this is not.** Hate and threats are semantic, and a pattern list is a
FIRST LINE with both false negatives and false positives. It catches the blunt
cases — which is most of what a support inbox actually receives — and it is
deliberately tuned to prefer a miss over a false positive: wrongly refusing an
angry-but-legitimate customer ("c'est scandaleux", "je vais porter plainte") is a
support failure that costs a real client, while a miss degrades to the model's
own refusal behaviour. Precision over recall, on purpose.
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


# Patterns are matched against `fold()`ed text: write them unaccented.
#
# Each list is narrow on purpose. The rejected-alternative worth naming: a bare
# `\bhais\b` for hate, or `\btuer\b` for violence. Both would fire on ordinary
# support traffic ("je hais devoir relancer", "il faut tuer la commande") — so
# the rules require the DEHUMANISING or TARGETED form, not the raw verb.
_HATE_PATTERNS: tuple[str, ...] = (
    # Dehumanisation — the reliable signal, and hard to say by accident.
    r"sous-\s?(?:etres?|humains?|hommes?)",
    r"(?:sont|etes|est)\s+(?:des\s+|un\s+)?(?:vermines?|parasites?|animaux)\b",
    r"devraient?\s+(?:tous?\s+)?disparaitre",
    # Slur openers. Kept to the constructions, not a slur dictionary: a project
    # that needs exhaustive coverage should put a real classifier behind the port.
    r"sale\s+(?:race|negre|arabe|juif|juive|pede|blanc|noir)",
    r"\bnegres?\b",
    # "go back to your country" — xenophobic imperative.
    r"retourne[zr]?\b.{0,30}\bdans\s+(?:ton|votre|leur)\s+pays",
)

_VIOLENCE_PATTERNS: tuple[str, ...] = (
    # Threats directed at a person: the verb ALONE is not enough, it must have a
    # target. "je vais te frapper" fires; "je vais frapper a la porte" does not.
    r"je\s+vais\s+(?:te|vous|le|la|les|lui)\s+(?:frapper|tuer|buter|defoncer|casser|crever|massacrer|etrangler)",
    r"je\s+vais\s+(?:tuer|buter|defoncer|massacrer|etrangler)\s+(?:ton|ta|tes|votre|vos|le|la|les)\b",
    r"je\s+(?:te|vous)\s+(?:retrouve|attends)\b.{0,30}\b(?:dehors|parking|chez\s+(?:toi|vous))",
    # Explicit self-labelled threats.
    r"c'?est\s+une\s+menace",
    r"(?:je\s+)?(?:te|vous)\s+menace\b",
    # Asking the agent to help harm someone else.
    r"comment\s+(?:faire\s+du\s+mal|blesser|tuer)\s+(?:a\s+)?(?:quelqu'?un|une?\s+personne)",
)

# Distinct from violence: the RESPONSE must differ (see the module docstring).
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

# Ordered: the first category that matches wins. Self-harm is checked BEFORE
# violence on purpose — "me faire du mal" must not be read as a threat and
# answered with a refusal, it must reach the supportive branch.
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
