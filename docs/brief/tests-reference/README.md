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
| `test_guardrails.py` | 🟠 **cahier des charges du chantier 2.** 3 critères sur 5 portent sur des fonctionnalités absentes ici (modération haine/violence/sexuel), ou **contredisent** une décision documentée : le hors-périmètre n'est pas bloqué (on répond « la FAQ ne le dit pas »), et le garde de sortie **caviarde** au lieu de bloquer |
| `test_mlops.py` | 🟠 **cahier des charges du chantier 3.** `run_eval` → `Scores`, `current_version`, `enforce_threshold`/`DeliveryBlocked`, `write_report` : rien de tout ça n'existe encore. `eval/` a les évaluateurs, il manque l'agrégation en note globale + le seuil bloquant + le rapport |
| `test_business.py` | ⛔ **à ne pas porter.** Exige le domaine Velmo : remboursements, plafond 50 €, modification d'article, table d'escalade, seed SQLAlchemy. Le porter voudrait dire construire le produit Velmo ici — précisément ce que la bifurcation vers l'agnosticisme a écarté |
| `conftest.py` | 🔎 gardé pour le contexte : il montre les fixtures que les tests supposent (`reference_agent` / `degraded_agent`, base SQLite seedée, `EchoLLM` hors ligne) |

## Deux dépendances externes qu'ils supposent

- `load_jsonl()` lit un dossier `eval/` à la racine du dépôt — **il n'existe pas
  ici**, donc `test_legitimate_messages_not_blocked` n'aurait rien à charger.
- `reference_agent.respond(customer_id, message)` est **synchrone**. La couture de
  ce dépôt est `stream_reply(message, *, user_id, thread_id)`, un générateur
  **asynchrone**.

## La correspondance R1 → R6 côté ce dépôt

Elle est tenue à jour dans [`docs/memoire.md`](../../memoire.md), section
« Conformité au cahier des charges mémoire ».
