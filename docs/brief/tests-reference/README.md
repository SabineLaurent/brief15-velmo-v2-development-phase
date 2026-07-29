# Tests d'acceptance Velmo — matériel de RÉFÉRENCE, pas une suite exécutable

Ces cinq fichiers viennent du dépôt de démarrage fourni en formation
(`brief15-velmo-v2-development-phase`). Ils sont conservés ici comme
**cahier des charges lisible** : ce sont les critères d'acceptance des trois
chantiers, écrits sous forme d'assertions, ce qui est souvent plus précis que la
prose des briefs.

⚠️ **Ils ne tournent pas dans ce dépôt, et ce n'est pas une question d'imports.**
Ils sont écrits contre les **classes internes de Velmo** (`velmo.agent.Agent`,
`velmo.memory.MemoryManager`, `velmo.guardrails.GuardrailEngine`, `velmo.mlops`,
les modèles SQLAlchemy de `velmo.db`), avec leurs signatures. Les faire pointer
vers `support_agent` transformerait le `ModuleNotFoundError` en `AttributeError`.

Ils sont donc **hors de la collecte pytest** — `testpaths = ["packages"]` dans le
`pyproject.toml` racine. Ne pas les déplacer sous `packages/*/tests/`.

## Ce que chacun vaut ici

| Fichier | Statut dans ce dépôt |
|---|---|
| `test_memory.py` | ✅ **le plus utile** — R1/R2/R3/R5 en assertions. R1 et R2 ont été **portés** contre les vraies coutures : `packages/support-agent/tests/test_memory_requirements.py` |
| `test_guardrails.py` | ✅ **chantier 2 fait (2026-07-29).** La modération haine/violence/sexuel existe (`guardrails/moderation.py`), et son corpus `guardrail_cases.jsonl` est **porté et exécuté** : `data/eval/guardrail_cases.jsonl` + `packages/support-agent/tests/test_moderation.py`. **30/35**, faux positifs **0/12**. Restent 5 cas `out_of_scope` écartés sous test — détail et argument : ROADMAP §Phase 12-D |
| `test_mlops.py` | 🟠 **cahier des charges du chantier 3.** `run_eval` → `Scores`, `current_version`, `enforce_threshold`/`DeliveryBlocked`, `write_report` : rien de tout ça n'existe encore. `eval/` a les évaluateurs, il manque l'agrégation en note globale + le seuil bloquant + le rapport |
| `test_business.py` | ⛔ **à ne pas porter.** Exige le domaine Velmo : remboursements, plafond 50 €, modification d'article, table d'escalade, seed SQLAlchemy. Le porter voudrait dire construire le produit Velmo ici — précisément ce que la bifurcation vers l'agnosticisme a écarté |
| `conftest.py` | 🔎 gardé pour le contexte : il montre les fixtures que les tests supposent (`reference_agent` / `degraded_agent`, base SQLite seedée, `EchoLLM` hors ligne) |

## Deux dépendances externes qu'ils supposent

- `load_jsonl()` lit un dossier `eval/` à la racine du dépôt. ✅ **Corrigé le
  2026-07-29** pour les garde-fous : `guardrail_cases.jsonl` (35 cas) a été porté
  en `data/eval/`, et c'est lui qui pilote `tests/test_moderation.py`. Les deux
  autres corpus du starter (`memory_cases.jsonl`, `quality_cases.jsonl`) sont
  **encore à porter** — matière du chantier 3.
- `reference_agent.respond(customer_id, message)` est **synchrone**. La couture de
  ce dépôt est `stream_reply(message, *, user_id, thread_id)`, un générateur
  **asynchrone**.

## La correspondance R1 → R6 côté ce dépôt

Elle est tenue à jour dans [`docs/memoire.md`](../../memoire.md), section
« Conformité au cahier des charges mémoire ».
