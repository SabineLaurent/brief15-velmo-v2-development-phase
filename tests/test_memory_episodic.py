"""Tests complémentaires — chantier Mémoire, étape 1b (long terme épisodique).

Couvre ce que le court terme seul (étape 1a) ne peut pas faire : retrouver un
souvenir sorti de la fenêtre `token_budget`. Distincts de
`tests/acceptance/test_memory.py`, le contrat figé des 3 chantiers, qui reste
rouge sur la persistance multi-session, l'isolation des faits durables et le
droit à l'oubli — hors périmètre de cette étape.
"""

from __future__ import annotations

from velmo.memory import MemoryManager


def test_recall_beyond_short_term_window():
    mm = MemoryManager(token_budget=20)
    user = "episodic-recall"
    mm.write(user, "Ma commande prioritaire est O-2024-0101 pour le maillot signe.", "Noté.")
    for i in range(5):
        mm.write(user, f"Message de remplissage numero {i} sans rapport.", f"Réponse {i}.")

    context = mm.read(user, "Quelle était ma commande prioritaire ?")

    assert not any("O-2024-0101" in content for _, content in context.history)
    assert any("O-2024-0101" in hit for hit in context.episodic)
    assert "O-2024-0101" in context.render()


def test_isolation_between_users():
    mm = MemoryManager()
    mm.write("marc", "Mon adresse est 12 rue des Lilas a Paris.", "Noté.")
    mm.write("sophie", "Mon adresse est 5 avenue des Roses a Lyon.", "Noté.")

    rendered_marc = mm.read("marc", "Quelle est mon adresse ?").render()
    rendered_sophie = mm.read("sophie", "Quelle est mon adresse ?").render()

    assert "Lilas" in rendered_marc
    assert "Roses" not in rendered_marc
    assert "Roses" in rendered_sophie
    assert "Lilas" not in rendered_sophie


def test_no_duplication_with_short_term_history():
    mm = MemoryManager()
    user = "no-dup"
    mm.write(user, "Je m'appelle Karim et j'aime le maillot brazil 1970.", "Enchanté Karim.")

    context = mm.read(user, "Karim maillot brazil 1970")

    assert any("Karim" in content for _, content in context.history)
    assert not any("Karim" in hit for hit in context.episodic)


def test_no_noise_for_unrelated_question():
    mm = MemoryManager(token_budget=20)
    user = "no-noise"
    mm.write(user, "Mon maillot préféré est le PSG 2006 signé par les joueurs.", "Noté.")
    for i in range(5):
        mm.write(user, f"Message de remplissage numero {i} sans rapport.", f"Réponse {i}.")

    context = mm.read(user, "Quel temps fait-il aujourd'hui ?")

    assert context.episodic == []
