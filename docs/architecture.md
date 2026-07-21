# 🏛️ Architecture — Le COMMENT

> Ce document décrit **la conception technique**. Pour le besoin métier, voir
> [`spec.md`](spec.md).

## 1. Vue d'ensemble

```
                    ┌─────────────────────────────────────────┐
   Client  ───────► │              AGENT (LangGraph)           │
   (message)        │  ┌────────────────────────────────────┐ │
                    │  │  Graphe : router → répondre / RAG   │ │
                    │  │           / escalader / outils      │ │
                    │  └───┬───────────┬───────────┬─────────┘ │
                    │      │           │           │           │
                    │   ┌──▼──┐   ┌────▼────┐  ┌───▼────┐      │
                    │   │ LLM │   │ Mémoire │  │  FAQ   │      │
                    │   │(abst)│  │ CT + LT │  │ (RAG)  │      │
                    │   └──┬──┘   └────┬────┘  └───┬────┘      │
                    └──────┼──────────┼───────────┼───────────┘
                           │          │           │
                    ┌──────▼───┐ ┌────▼─────┐ ┌───▼──────────┐
                    │ Provider │ │Checkpoint│ │ Vector store │
                    │ (via .env)│ │ + Store  │ │ + embeddings │
                    └──────────┘ └──────────┘ └──────────────┘
```

Chaque bloc = un dossier dans `src/support_agent/`. On les construit un par un
(voir `ROADMAP.md`).

## 2. ⭐ Le cœur agnostique : la couche LLM

**Principe :** le code applicatif ne connaît qu'une interface abstraite,
`BaseChatModel` (commune à tous les providers LangChain). Le choix du provider
est une donnée de **configuration**, pas de code.

```
config (.env)  ──►  llm/factory.py : get_chat_model()  ──►  BaseChatModel
   LLM_PROVIDER=mistral                                        (utilisé partout)
```

Trois cas gérés par la factory :

1. **Provider standard** (mistral, groq, google_genai, azure_ai…)
   → on délègue à `init_chat_model("provider:model")`, le sélecteur natif de
   LangChain. Rien à écrire de plus.
2. **API OpenAI-compatible** (beaucoup d'APIs maison ou tierces le sont)
   → `ChatOpenAI(base_url=..., api_key=...)`. Juste de la config.
3. **API 100 % custom** (non standard)
   → un adaptateur `llm/adapters/custom.py` : une sous-classe `BaseChatModel`
   (~30 lignes) qui traduit notre appel vers ton API. **Seul** endroit qui
   connaît les détails de cette API ; le reste de l'app l'ignore.

> 💡 **C'est ça, l'agnosticisme** : swaper Mistral → Groq → Foundry → API maison
> = changer `LLM_PROVIDER` dans `.env`. Zéro refacto.

Même logique pour les **embeddings** (RAG) : `llm/embeddings.py` renvoie une
interface `Embeddings` abstraite, choisie par config.

## 3. Le modèle de mémoire (deux niveaux)

LangGraph distingue nettement deux mémoires — c'est un concept clé à comprendre :

| | **Court terme** | **Long terme** |
|---|---|---|
| Portée | Une conversation (thread) | Tous les threads d'un utilisateur |
| Contient | L'historique des messages, l'état | Faits durables, préférences, résumés |
| Mécanisme LangGraph | `Checkpointer` | `Store` |
| Clé d'accès | `thread_id` | `namespace` (ex: `("user", user_id)`) |
| Analogie | La RAM de la conversation | Le disque dur de l'agent |

- **Court terme (Phase 3)** : un `checkpointer` sauvegarde l'état du graphe à
  chaque étape, indexé par `thread_id`. Reprendre une conversation = rejouer le
  même `thread_id`.
- **Long terme (Phase 5)** : un `store` clé/valeur (avec recherche sémantique
  optionnelle) partagé entre threads, rangé par `namespace`.

Implémentations interchangeables : en mémoire vive pour apprendre
(`InMemorySaver`, `InMemoryStore`), puis backend persistant (SQLite/Postgres)
pour la prod — **sans changer le code de l'agent**.

## 4. La base de connaissance FAQ (RAG) — Phase 4

Pipeline classique de Retrieval-Augmented Generation :

```
Documents FAQ ─► découpage (chunking) ─► embeddings ─► vector store
                                                             │
Question client ─► embedding ─► recherche similarité ◄───────┘
                                     │
                                     ▼
                         chunks pertinents + question ─► LLM ─► réponse sourcée
```

Le vector store est **Chroma en mode embarqué**, persisté dans
`TEMP/database/chroma` (`KNOWLEDGE_INDEX_DIR`). En prod : **la même classe
`Chroma`**, en mode serveur (`host`/`port` au lieu de `persist_directory`) — la
bascule ne touche qu'`ingest.py`.

**L'index est une projection, pas une source.** La FAQ versionnée vit dans
`data/faq/` ; l'index n'en est qu'une transformation, reconstructible à volonté —
d'où son emplacement sous `TEMP/` (jetable) et non `data/`.

Persister un index crée un risque : servir des vecteurs qui ne correspondent plus
à la source. Un agent qui répond depuis une FAQ périmée **ne lève aucune erreur** —
il a l'air de marcher. D'où une **empreinte** (`knowledge/fingerprint.py`) écrite
dans le dossier d'index et vérifiée au démarrage :

```
empreinte = hash(  fichiers FAQ (nom + contenu)          ← CE QU'on indexe
                 + provider & modèle d'embeddings         ← COMMENT on l'indexe
                 + chunk_size & chunk_overlap          )
```

Elle couvre les trois façons d'invalider l'index : éditer/ajouter/renommer un
fichier, changer de modèle (autre espace vectoriel ⇒ vecteurs incomparables),
changer le découpage. Identique ⇒ on réutilise (**zéro appel d'embedding**) ;
différente ou illisible ⇒ reconstruction. Le doute conduit **toujours** à
reconstruire : le coût est un ré-embedding, le risque évité est une réponse fausse.

Forcer une reconstruction : `make reindex` (app arrêtée — Chroma garde un client
en cache par dossier et par process).

## 5. Orchestration : LangGraph — Phase 6

L'agent est un **graphe d'états** (`StateGraph`) : des **nœuds** (étapes) reliés
par des **arêtes** (transitions), certaines **conditionnelles** (routage). Cela
rend le comportement explicite, débogable et traçable — contrairement à une
simple chaîne linéaire.

Au début (Phases 1–5) on a utilisé le raccourci `create_agent` (agent
préfabriqué) ; depuis la Phase 6 on a « ouvert le capot » avec un `StateGraph`
custom (package `src/support_agent/graph/`) : un nœud `router` classe l'intention
(LLM à sortie structurée), puis une arête conditionnelle aiguille vers `answer`
(petit talk), `support` (boucle ReAct explicite `model` ⇄ `ToolNode` : FAQ +
mémoire) ou `escalate` (handoff humain — remplacé par un vrai `interrupt` en
Phase 7).

## 6. Configuration & agnosticisme projet

- Toute la config passe par `config.py` (Pydantic Settings) qui lit `.env`.
- Rien de spécifique à un projet n'est codé en dur : la FAQ, le provider, les
  clés, les modèles… tout est injecté.
- Brancher l'agent sur un nouveau projet = fournir une nouvelle FAQ + un `.env`.

## 7. Observabilité : LangSmith — Phase 2

Variables d'env `LANGSMITH_*` → chaque exécution (appels LLM, retrieval,
décisions du graphe) est tracée automatiquement. Indispensable pour comprendre
*pourquoi* l'agent a répondu ainsi.

## 8. Décisions techniques

| Sujet | Choix | Raison |
|---|---|---|
| Langage | Python 3.12 | Compat maximale de l'écosystème lang* |
| Gestion projet | `uv` | Rapide, moderne, lockfile reproductible |
| Config | Pydantic Settings | Typée, validée, lit `.env` |
| Orchestration | LangGraph | Stateful, mémoire, human-in-the-loop natifs |
| Abstraction LLM | `BaseChatModel` + factory | Cœur de l'agnosticisme |

## 9. Structure du dépôt (mono-repo + uv workspace)

> Traduction **physique** du choix stratégique « un dépôt, des scopes découplés »
> (le *pourquoi* est dans [`vision.md`](vision.md)). Ici : le *comment* factuel.

**Mécanique uv workspace.** Un *workspace* = plusieurs packages Python dans un même
dépôt, gérés d'un bloc : chaque package a son propre `pyproject.toml` (ses
dépendances), mais **tous partagent un unique `uv.lock` et un unique `.venv`**. Un
package en dépend d'un autre via `dep = { workspace = true }` — la dépendance
interne est alors éditable, sans publication. On garde `uv sync` / `uv run` /
`make …` depuis la racine, comme aujourd'hui.

> 📦 **Anatomie d'un membre** (les 3 niveaux `support-agent/src/support_agent/`,
> et le pourquoi du tiret vs underscore) : voir
> [`anatomie-package-workspace.md`](anatomie-package-workspace.md).

**État actuel** : migration B1.0 faite — le cœur vit dans
`packages/support-agent/` (workspace root virtuel à la racine + le membre
`support-agent`). Scopes B (frontend) et C (backend) pas encore matérialisés.

**Structure cible** (appliquée à l'étape **B1.0** du scope Frontend, voir
`ROADMAP.md`) :

```
agnostic-consumer-support-AI-agent/     ← racine = workspace root
├── pyproject.toml          # [tool.uv.workspace] members = ["packages/*"]
├── uv.lock                 # UN lockfile partagé
├── Makefile                # porte d'entrée unique (make run / ui / test…)
├── docs/                   # docs partagées (vision, spec, archi, roadmap)
└── packages/
    ├── support-agent/      # Scope A — le cœur (l'actuel src/support_agent)
    │   ├── pyproject.toml
    │   └── src/support_agent/
    ├── frontend/           # Scope B — la coquille (Chainlit → puis web)
    │   ├── pyproject.toml   # dépend de support-agent en workspace = true
    │   └── …
    └── backend/            # Scope C — plus tard (vrai SI, Postgres…)
```

**Migration** (physique, à faire tant que le cœur est seul = moins de churn) :
déplacer `src/support_agent/` → `packages/support-agent/`, découper le
`pyproject.toml` (un « workspace root » + un par package), ajuster `Makefile` et
la section « Structure » de `CLAUDE.md`. Les imports internes
(`from support_agent…`) et le lockfile unique restent inchangés.

**Évolution polyglotte** (si un front JS/React arrive) : uv workspace pour le
Python + un dossier app JS ; task-runner léger (**Turborepo / Nx**) *seulement si
besoin*. On **évite Bazel / Pants** (surdimensionné pour un solo). Reste léger :
`Makefile` + CI par chemin.

> 🧩 Le lockfile mutualisé du workspace **n'enferme dans aucune topologie de
> déploiement** : un lockfile → N images conteneurs indépendantes ; front et BDD
> restent interchangeables (API réseau + ports + variables d'env). Détail dans
> [`workspace-et-deploiement.md`](workspace-et-deploiement.md).

## 10. Principe de code à appliquer

- clean code;
- SoC;
- DRY;
- KISS et en tout cas pas d'over-engineering;
- bonnes pratiques PEP.
