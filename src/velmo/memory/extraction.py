# ===============================================================================
# VELMO’S MEMORY BRAIN
# Extraction des faits durables
# ===============================================================================

"""Repérage déterministe des préférences durables dans un message client.

Isolé de l'orchestrateur (`manager.py`) : c'est l'heuristique la plus susceptible
d'évoluer (nouveaux motifs) ou de produire des faux positifs, donc elle vit à
part. Motifs volontairement conservateurs : mieux vaut rater un fait que d'en
inventer un. Ce qui n'est pas capté ici reste rappelable en épisodique.
"""

from __future__ import annotations

import re

# Motifs déterministes d'extraction de faits durables depuis un message client.
# Tous ancrés sur des mots entiers (\b) pour éviter les faux positifs de
# sous-chaîne : « mail » dans « maillot », « porte » dans « comporte ».
_SIZE_RE = re.compile(r"\b(XXL|XL|S|M|L)\b")
# La taille n'est retenue que si le client parle de LA SIENNE (première
# personne), pas de la taille d'un produit (« dispo en taille M ? »).
_TAILLE_CTX_RE = re.compile(
    r"\bje\s+(?:porte|prends|fais|mets)\b|\bma\s+taille\b", re.IGNORECASE
)
_CLUBS_RE = re.compile(
    r"clubs?[^:]*?(?:sont|:|pr[ée]f[ée]r[ée]s?)\s+(.+)", re.IGNORECASE
)
# Connecteur résiduel laissé en tête de la valeur clubs (« préférés sont X »).
_CLUBS_LEAD_RE = re.compile(r"^sont\s+", re.IGNORECASE)
_SEGMENT_RE = re.compile(r"\brevendeurs?\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b(?:e-?mail|mail)\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:t[ée]l[ée]phone|appel)", re.IGNORECASE)


def extract_facts(message: str) -> dict[str, str]:
    """Repère les préférences durables d'un message (taille, clubs, segment,
    canal). Motifs volontairement conservateurs : mieux vaut rater un fait que
    d'en inventer un. Ce qui n'est pas capté ici reste rappelable en épisodique."""
    facts: dict[str, str] = {}

    if _TAILLE_CTX_RE.search(message):
        size = _SIZE_RE.search(message)
        if size:
            facts["taille"] = size.group(1)

    clubs = _CLUBS_RE.search(message)
    if clubs:
        value = _CLUBS_LEAD_RE.sub("", clubs.group(1).strip(" .!?:")).strip()
        if value:
            facts["clubs"] = value

    if _SEGMENT_RE.search(message):
        facts["segment"] = "revendeur"

    if _EMAIL_RE.search(message):
        facts["canal"] = "email"
    elif _PHONE_RE.search(message):
        facts["canal"] = "téléphone"

    return facts
