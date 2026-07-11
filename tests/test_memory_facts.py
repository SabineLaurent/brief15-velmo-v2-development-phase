"""Tests complémentaires — chantier Mémoire, étape 1c (long terme factuel).

Couvre ce que ni le court terme (1a) ni l'épisodique (1b) ne peuvent garantir :
la persistance de faits structurés au-delà de la durée de vie d'un
`MemoryManager`, et un droit à l'oubli qui purge effectivement les trois
étages. Distincts de `tests/acceptance/test_memory.py`, le contrat figé des 3
chantiers.
"""

from __future__ import annotations

from velmo.memory import MemoryManager


def test_fact_persists_across_manager_instances():
    session1 = MemoryManager()
    session1.remember_fact("facts-marc", "pointure", "L")

    session2 = MemoryManager()
    facts = session2.read("facts-marc", "peu importe").facts
    assert facts["pointure"] == "L"


def test_fact_overwrite_keeps_latest_value():
    mm = MemoryManager()
    mm.remember_fact("facts-overwrite", "pointure", "M")
    mm.remember_fact("facts-overwrite", "pointure", "L")

    facts = mm.read("facts-overwrite", "peu importe").facts
    assert facts["pointure"] == "L"


def test_fact_isolation_between_users():
    mm = MemoryManager()
    mm.remember_fact("facts-marc-iso", "commande", "O-2024-0103")
    mm.remember_fact("facts-sophie-iso", "commande", "O-2024-0107")

    assert mm.read("facts-marc-iso", "?").facts == {"commande": "O-2024-0103"}
    assert mm.read("facts-sophie-iso", "?").facts == {"commande": "O-2024-0107"}


def test_forget_removes_matching_fact_only():
    mm = MemoryManager()
    user = "facts-forget-scoped"
    mm.remember_fact(user, "adresse", "12 rue des Lilas")
    mm.remember_fact(user, "pointure", "L")

    removed = mm.forget(user, "adresse")

    assert removed == 1
    facts = mm.read(user, "?").facts
    assert "adresse" not in facts
    assert facts["pointure"] == "L"


def test_forget_purges_facts_history_and_episodic_together():
    mm = MemoryManager()
    user = "facts-forget-all-tiers"
    mm.remember_fact(user, "adresse", "12 rue des Lilas")
    mm.write(user, "Mon adresse de livraison est 12 rue des Lilas.", "C'est noté.")

    removed = mm.forget(user, "adresse")

    assert removed >= 2  # au moins le fait + le tour court terme
    rendered = mm.read(user, "Mon adresse ?").render()
    assert "rue des Lilas" not in rendered
    assert "adresse" not in mm.read(user, "?").facts
