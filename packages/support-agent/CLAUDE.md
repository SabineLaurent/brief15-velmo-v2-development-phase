# CLAUDE.md — `packages/support-agent` (le cœur de l'agent, scope A)

> Complément **local** au `CLAUDE.md` racine (toujours chargé : agnosticisme LLM,
> fr/en, `uv`/Python 3.12, structure workspace). Ce fichier n'est lu **qu'en
> travaillant dans ce package**. Il **précise** ; il ne remplace pas le root, et
> ne duplique pas les `docs/` (il y renvoie).

## Le rôle de ce package en une phrase

Le **cerveau** : l'agent de support (LangChain + LangGraph + LangSmith). **Toute**
la logique lang\* vit ici, et **nulle part ailleurs**. Sa **porte de sortie
unique** = `api.py` (`stream_reply`).

## Invariant n°1 (agnosticisme LLM) — où il vit concrètement

Le root pose la règle non négociable ; **elle se joue dans ce package** :

- La **seule** place qui instancie un provider = `llm/factory.py`
  (`get_chat_model` / `get_chat_model_fallbacks`, via `init_chat_model`).
- Nœuds, tools et couture prennent leur modèle par `get_chat_model()` — **jamais**
  de `ChatOpenAI(...)`/`ChatMistralAI(...)` en dur ailleurs.
- Changer de provider = une variable `.env`, pas du code.

## La couture (`api.py`) — la seule chose qu'un front voit

- `stream_reply(message, *, user_id, thread_id)` **cache entièrement** LangGraph
  (async generator de tokens). Rien de lang\* ne fuite.
- **Filtrage** : on ne streame que les nœuds face-client
  (`CUSTOMER_FACING_NODES = {answer, model}`) ; le **`router`** (sortie
  structurée) ne fuite **jamais**. Ce filtrage vit dans la couture, pas dans le front.
- Pont **sync→async par thread** pour garder le checkpointer **sync** (donc le
  backend `sqlite` reste valide, pas d'`astream`/`AsyncSqliteSaver` imposé).
  Répartition des rôles streaming : [`docs/streaming.md`](../../docs/streaming.md).

## Carte du package (le COMMENT détaillé → [`docs/architecture.md`](../../docs/architecture.md))

`config.py` (Settings .env) · `llm/` factory + embeddings (agnostique) ·
`memory/` court terme (checkpointer / `thread_id`) + long terme (store / `user_id`) ·
`knowledge/` RAG FAQ · `graph/` nœuds + arêtes + `state` · `guardrails/`
(kill switch) · `actions/` (port métier) · `eval/` · `agent.py` (CLI) ·
`api.py` (la couture).

## Faits opérationnels à garder en tête

- **FAQ = index Chroma PERSISTANT** (`knowledge/ingest.py`), dans
  `TEMP/database/chroma`. Au démarrage on **réutilise** l'index si son
  **empreinte** correspond (fichiers FAQ + modèle d'embeddings + chunking) —
  sinon reconstruction automatique. Donc : **0 appel d'embedding à chaud**, et
  jamais de réponse servie depuis une FAQ périmée (`knowledge/fingerprint.py`).
  Forcer : `make reindex` (app arrêtée). Pas de re-vectorisation *par question*
  (seule la question est embeddée pour la recherche).
- **Prod = même classe `Chroma`, en mode serveur** (`host`/`port` au lieu de
  `persist_directory`) : la bascule reste **un seul fichier** (`ingest.py`).
  ⚠️ Chroma met un client en cache **par dossier et par process** : supprimer le
  dossier d'index pendant que l'app tourne casse le client (`readonly database`).
- **Persistance mémoire** (checkpointer + store) : switch `memory` / `sqlite` /
  `postgres` via `PERSISTENCE_BACKEND` (`.env`) — pas de code.
- **Guardrails** : kill switch `GUARDRAILS_ENABLED` ; off = graphe identique à avant.
- Les providers **auto-découvrent** leurs `*_API_KEY` depuis `.env`.

## Méthode dans ce package

- **Context7 avant tout code lang\*** (langchain / langgraph / langsmith) — ces
  API bougent vite. C'est ici que la règle s'applique le plus.
- Lancer depuis la **racine** : `make run` / `make test` / `make check`.
- Une phase à la fois — la feuille de route scope A est [`ROADMAP.md`](../../ROADMAP.md)
  (racine) ; le scope B a [`docs/roadmap-frontend.md`](../../docs/roadmap-frontend.md).
