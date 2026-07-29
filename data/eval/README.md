# `data/eval/` — les corpus d'acceptance, copiés VERBATIM

Ces trois fichiers viennent du starter de formation
(`brief15-velmo-v2-development-phase/eval/`). Ce sont les critères d'acceptance
des trois chantiers, écrits **en données** plutôt qu'en prose — souvent plus
précis que le brief lui-même. Ce projet les **exécute** au lieu de les
paraphraser.

| Fichier | Cas | Chantier | Exécuté par |
|---|---|---|---|
| `guardrail_cases.jsonl` | 35 | 2 — garde-fous | `tests/test_moderation.py` (hors ligne) |
| `memory_cases.jsonl` | 12 | 1 — mémoire (R1/R2/R3/R5) | `tests/test_memory_cases.py` (hors ligne) |
| `quality_cases.jsonl` | 8 | 3 — qualité des réponses | `tests/test_quality_cases.py` (intégration) |

Un seul parseur les lit : `support_agent/eval/corpus.py`.

## ⚠️ Règle : on ne touche pas à ces fichiers

Ils sont **byte-identiques** au starter (vérifiable au `shasum`). Deux raisons :

1. Un `diff` contre la source reste lisible — on voit tout de suite si un critère
   a bougé côté formation.
2. **Personne ne peut adoucir un critère en modifiant l'attente au lieu du code.**
   C'est la seule garantie qui rende le mot « acceptance » honnête.

Tout ce que ce projet décide **par-dessus** vit donc dans le code, où c'est
relisible en revue :

- `ACCEPTED_PARAPHRASES` (`eval/corpus.py`) — les attentes écrites en *notation*
  (`prepared`, `J+2`) que la prose française développe légitimement ;
- `UNSUPPORTED_QUALITY_CASES` — le cas que le port métier ne peut pas répondre
  (`q-stock` : pas de capacité stock), épinglé par un test structurel ;
- `DELIBERATELY_NOT_BLOCKED` (`tests/test_moderation.py`) — les catégories dont
  le blocage est **écarté en l'argumentant**, et asserté comme tel.

## Ce qui n'est pas porté

`test_business.py` du starter et son domaine (remboursements, plafond 50 €, table
d'escalade) : le porter voudrait dire construire le produit Velmo ici, ce que la
bifurcation vers l'agnosticisme a précisément écarté. Détail :
[`docs/brief/tests-reference/README.md`](../../docs/brief/tests-reference/README.md).
