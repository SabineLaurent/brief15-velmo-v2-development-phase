"""Tests complémentaires — chantier Mémoire, étape 1a (court terme).

Ces tests couvrent le comportement du tampon en RAM introduit dans
`MemoryManager` : rappel intra-session, isolation par utilisateur, respect du
`token_budget`. Ils sont distincts de `tests/acceptance/test_memory.py`, qui
est le contrat figé des 3 chantiers (mémoire/garde-fous/MLOps) et qui reste
rouge à ce stade : ce contrat exige persistance multi-session et rappel
au-delà de la fenêtre courte, construits dans une étape ultérieure.
"""

from __future__ import annotations

from velmo.memory import MemoryManager


def test_recall_within_session():
    mm = MemoryManager()
    user = "short-term-recall"
    mm.write(user, "Ma commande est O-2024-0101.", "Je regarde ça.")
    mm.write(user, "Merci.", "Avec plaisir.")

    rendered = mm.read(user, "Rappelle-moi ma commande.").render()
    assert "O-2024-0101" in rendered
    assert "Avec plaisir" in rendered


def test_isolation_between_users_same_manager():
    mm = MemoryManager()
    mm.write("marc", "Ma commande prioritaire est O-2024-0103.", "Noté.")
    mm.write("sophie", "Ma commande prioritaire est O-2024-0107.", "Noté.")

    rendered_marc = mm.read("marc", "Quelle est ma commande ?").render()
    rendered_sophie = mm.read("sophie", "Quelle est ma commande ?").render()

    assert "O-2024-0103" in rendered_marc
    assert "O-2024-0107" not in rendered_marc
    assert "O-2024-0107" in rendered_sophie
    assert "O-2024-0103" not in rendered_sophie


def test_token_budget_trims_oldest_turns():
    # Vérifie le tampon court terme spécifiquement (`.history`), pas le rendu
    # complet : depuis l'étape 1b, la mémoire épisodique peut légitimement
    # faire réapparaître un ancien tour dans `.episodic` — c'est son rôle.
    mm = MemoryManager(token_budget=20)
    user = "budget-trim"
    mm.write(user, "premier message assez long pour peser dans le budget", "ok")
    mm.write(user, "deuxieme message assez long pour peser dans le budget", "ok")
    mm.write(user, "troisieme message assez long pour peser dans le budget", "ok")

    history = mm.read(user, "message ?").history
    contents = [content for _, content in history]
    assert any("troisieme" in c for c in contents)
    assert not any("premier" in c for c in contents)
