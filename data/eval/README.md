# `data/eval/` — corpus d'évaluation

Jeux de cas d'acceptance de l'agent, au format JSONL. Ils sont lus par un unique
parseur, `support_agent/eval/corpus.py`.

| Fichier | Cas | Dimension | Exécuté par |
|---|---|---|---|
| `guardrail_cases.jsonl` | 35 | Garde-fous | `tests/test_moderation.py` (hors ligne) |
| `memory_cases.jsonl` | 12 | Mémoire (R1/R2/R3/R5) | `tests/test_memory_cases.py` (hors ligne) |
| `quality_cases.jsonl` | 8 | Qualité des réponses | `tests/test_quality_cases.py` (intégration) |

Les tests valident chaque cas individuellement ; `make score`
(`support_agent/eval/mlops.py`) en dérive une note par dimension et une porte de
livraison.

## Modification des corpus

Ces fichiers sont figés : ils constituent la référence d'acceptance et ne doivent
pas être édités pour faire passer un test. Les ajustements propres au projet
vivent dans le code, où ils sont relisibles en revue :

- `ACCEPTED_PARAPHRASES` (`eval/corpus.py`) — équivalences entre les attentes
  écrites en notation (`prepared`, `J+2`) et leur formulation en français ;
- `UNSUPPORTED_QUALITY_CASES` — cas hors du périmètre du port métier
  (`q-stock` : pas de capacité stock), épinglé par un test structurel ;
- `DELIBERATELY_NOT_BLOCKED` (`tests/test_moderation.py`) — catégories
  volontairement non bloquées, assertées comme telles.

## `../eval-baseline.json`

La baseline est délibérément hors de ce dossier : les corpus ne changent pas, la
baseline est réécrite sur décision explicite. Elle enregistre le nombre de cas
réussis au dernier niveau accepté.

```bash
make score                          # compare à la baseline, bloque en cas de régression
make score ARGS=--update-baseline   # accepte le run courant comme nouveau niveau
```

Deux propriétés :

- elle porte l'empreinte des corpus (`corpus`) ; une baseline enregistrée sur
  d'autres cas est écartée avec un motif plutôt que comparée silencieusement ;
- elle ne change que sur un geste explicite, sinon une dégradation progressive ne
  franchirait jamais de seuil.

La baseline versionnée ne contient que les dimensions déterministes (mémoire,
garde-fous), les seules que la CI peut recalculer. Enregistrer une note de qualité
demande `make score ARGS='--live --update-baseline'`, en local.
