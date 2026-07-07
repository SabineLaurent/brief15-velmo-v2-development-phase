# First sight — Velmo V2

_Premier regard, 2026-07-06._

## Vue d'ensemble

**Velmo V2** est un assistant de support pour une boutique en ligne de **maillots
de foot collector** (rééditions vintage, pièces signées, éditions limitées).
L'agent traite le support de niveau 1 : statut/suivi de commande, disponibilité
stock, modification/annulation avant expédition, retours, remboursements simples,
FAQ — en conservant le contexte du client dans le temps.

Le projet est un **scaffold volontairement partiel** : le socle métier (base +
outils) est fonctionnel et testé, et **trois chantiers restent à construire** :
mémoire durable, garde-fous de contenu, chaîne qualité MLOps. Les stubs de ces
trois modules exposent déjà leur surface publique (signatures stables) mais
renvoient des valeurs no-op ou lèvent `NotImplementedError`.

### Stack

| Couche | Techno |
|--------|--------|
| Langage | Python 3.11 (géré par `uv`) |
| Base relationnelle | PostgreSQL + SQLAlchemy 2 + Alembic |
| FAQ / vectoriel | Chroma + embeddings `intfloat/multilingual-e5-small` (extra `vector`) |
| LLM | Azure AI Inference — Kimi-K2.6 (extra `llm`) |
| CI | GitHub Actions (`.github/workflows/quality.yml`) |
| Qualité | pytest, ruff, mypy `strict` |

**Repli hors-ligne** : le cœur tourne sans aucun service externe — SQLite en
mémoire, FAQ locale (TF-IDF léger), LLM en écho. Les intégrations réelles
s'activent par variables d'environnement (`DB_URL`, `CHROMA_URL`,
`AZURE_AI_INFERENCE_ENDPOINT`). C'est ce qui rend la suite de tests exécutable
sans dépendances lourdes.

## Flux principal — `agent.respond(user_id, message)`

Le pipeline est fixé dans `src/velmo/agent.py` :

```
message
  │
  ├─▶ guardrails.check_input(message)      ── si bloqué → refus poli + mémoire.write → STOP
  │
  ├─▶ memory.read(user_id, message)        ── reconstitue le contexte (historique, faits, épisodique)
  │
  ├─▶ _handle(user_id, message)            ── ROUTAGE DÉTERMINISTE (regex + mots-clés)
  │     ├─ O-XXXX-XXXX + "annul"/"adresse"/"taille"/"retour"/"rembours" → outil (avec confirmation)
  │     ├─ O-XXXX-XXXX + "suivi"/"colis"/"livr" → track_shipment
  │     ├─ O-XXXX-XXXX seul               → get_order
  │     ├─ "dispo"/"stock"/"en taille"    → check_stock (résolution d'alias produit)
  │     ├─ mots-clés FAQ                  → search_kb (RAG)
  │     └─ sinon                          → llm.invoke (fallback conversationnel)
  │
  ├─▶ guardrails.check_output(answer)      ── si bloqué → réponse remplacée par un refus
  │
  └─▶ memory.write(user_id, message, answer)
      return answer
```

Point notable : **le routage n'est pas piloté par le LLM** mais par des regex et
des mots-clés (`ORDER_RE`, `SIZE_RE`, `AMOUNT_RE`, `_FAQ_KEYWORDS`, `_ALIASES`).
Le LLM n'est qu'un repli pour les messages non routés. Les actions sensibles
(annulation, changement de taille/adresse, remboursement) passent par une étape
de **confirmation explicite** (`_confirm_or_act`, déclenchée par « je confirme »).

## Module par module

### Socle fonctionnel (implémenté, testé)

- **`db.py`** — Schéma SQLAlchemy 2 complet : `Customer`, `Product`,
  `ProductVariant`, `Order`, `OrderItem`, `Shipment`, `Return`, `Refund`,
  `Escalation`. IDs lisibles (`O-2024-0103`, `C-marc-dubois`, `mu-1999-treble`).
  Fabriques `session_factory()` (Postgres) et `fresh_sqlite_session()` (tests).
- **`tools/`** — 10 outils métier découvrables, chacun encapsulant ses règles :
  - `orders.py` : `get_order`, `track_shipment`, `update_order_item`,
    `update_shipping_address`, `cancel_order`.
  - `refunds.py` : `trigger_refund` (plafond 50 € → escalade au-delà).
  - `returns.py` : `create_return`. `catalog.py` : `check_stock`.
  - `escalation.py` : `escalate_to_human`. `kb.py` : `search_kb`.
  - `_common.py` : constantes partagées — `REFUND_CAP = 50.0`,
    `MODIFIABLE_STATUSES = {paid, prepared}`, `RETURNABLE_STATUSES = {delivered}`,
    et `owned_order()` qui garantit l'**isolation par client** (R3).
- **`kb_store.py`** — Deux backends FAQ interchangeables : `LocalKB` (TF-IDF
  pondéré IDF, hors-ligne) et `ChromaKB` (sémantique). `get_kb()` choisit selon
  `CHROMA_URL`. Les docs vivent dans `kb/docs/*.md` (17 fiches FAQ).
- **`llm.py`** — `EchoLLM` (repli) et `AzureLLM` (Kimi-K2.6). Import Azure différé.
- **`cli.py`** — REPL de conversation (`--user`, défaut `C-marc-dubois`).
- **`sampledata.py`** — jeu de données de référence (catalogue, clients, ~14 commandes).

### Chantiers à construire (stubs)

- **`memory/`** — `MemoryManager` (`read`, `write`, `remember_fact`, `forget`,
  `inspect`) + `MemoryContext` (history / facts / episodic, avec `.render()`).
  Toutes les méthodes sont **no-op** aujourd'hui. Doit satisfaire 6 exigences
  R1–R6 : rappel longue conversation, persistance multi-session, **isolation par
  user_id**, tenue de la fenêtre de contexte (`token_budget=2000`), droit à
  l'oubli, traçabilité.
- **`guardrails/`** — `GuardrailEngine.check_input` / `check_output` renvoient
  actuellement `allow` inconditionnel. 7 catégories à couvrir : `hate`,
  `violence`, `sexual`, `pii`, `out_of_scope`, `prompt_injection`, `secret_leak`.
  Doit journaliser chaque décision dans `events` et rester sous un seuil de faux
  positifs (≤ 10 % sur les messages légitimes).
- **`mlops/`** — `run_eval`, `enforce_threshold`, `write_report`,
  `current_version` lèvent `NotImplementedError`. Doit produire des `Scores`
  (mémoire / garde-fous / qualité / note globale + block_rate, false_positive_rate,
  latence, coût), **bloquer la CI** (`DeliveryBlocked`) sous seuil, et écrire un
  rapport lisible.

### Support

- **`eval/*.jsonl`** — jeux de cas : `guardrail_cases` (35), `memory_cases` (12),
  `quality_cases` (8). Ce sont les contrats d'évaluation des trois chantiers.
- **`tests/acceptance/`** — `test_business` (métier, passe), `test_memory`,
  `test_guardrails`, `test_mlops` (chantiers, échouent tant que non implémentés).
- **`scripts/`** — `seed.py` (Postgres), `seed_kb.py` (Chroma). **`alembic/`** —
  migration `0001_initial`. **Docker** — `Dockerfile`, `docker-compose.yml` (app
  + postgres + chroma).
- **`docs/reco_expert.md`** — note de cadrage : stack imposée + 3 exigences non
  négociables (mémoire, garde-fous, qualité mesurée).

## État des lieux — suite de tests (`pytest tests/`)

**7 passent, 12 échouent** (état attendu du scaffold) :

- ✅ **`test_business.py` (7/7)** — les règles métier sont sûres *par
  construction*, dans les outils : isolation par client, blocage des
  modifications après expédition (escalade + trace), plafond de remboursement,
  pas de fabulation en rupture de stock.
- ❌ **`test_memory.py` (4)** — rappel > 30 tours, persistance multi-session,
  isolation, droit à l'oubli → `MemoryManager` no-op.
- ❌ **`test_guardrails.py` (5)** — blocage hate/violence/sexual, résistance à
  l'injection, PII en sortie, hors-périmètre, faux positifs → `GuardrailEngine`
  laisse tout passer.
- ❌ **`test_mlops.py` (3)** — production de notes, blocage de régression,
  rapport → `NotImplementedError`.

### Ce qui marche

Parler à la base et à la FAQ : statut/suivi de commande, disponibilité stock
(avec alias produits), actions encadrées avec confirmation et escalade, réponses
FAQ sourcées. Toutes les garanties métier tiennent avant même les garde-fous de
contenu.

### Ce qui reste à faire

Les **trois chantiers du brief**, dans l'ordre des exigences de `reco_expert.md` :
1. **Mémoire** durable et isolée par utilisateur (R1–R6).
2. **Garde-fous** de contenu en entrée *et* sortie, journalisés, à faible taux de
   faux positifs.
3. **MLOps** : évaluation reproductible, note globale, seuil bloquant en CI,
   rapport de suivi.

## Commandes utiles

```bash
make up / make down     # docker compose (app + postgres + chroma)
make migrate / make seed / make seed-kb
make chat               # REPL de conversation
make test               # suite d'acceptance + tests métier
make fmt / make lint / make typecheck
make eval               # python -m velmo.mlops.score (à construire)
```
