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
- ⭐ **Un provider = un extra** (`[project.optional-dependencies]`) : `mistral`,
  `groq`, `google`, `azure`. Un install ne paie pas les SDK qu'il n'utilise pas
  (`uv sync --extra groq`). Exception : `langchain-openai` est une dépendance **de
  base**, car `llm/embeddings.py` en dépend pour le chemin `openai_compatible`,
  défaut actuel de la FAQ **et** de la mémoire long terme.
- ⚠️ **Règle de non-dérive** : ajouter une entrée à un `_PROVIDER_ALIASES`
  (`llm/factory.py` ou `llm/embeddings.py`) oblige à déclarer son extra dans
  `pyproject.toml` **et** dans `llm/_extras.py`, dans le MÊME commit. Sinon on
  annonce dans `.env` un provider que l'install ne peut pas construire.
  `tests/test_llm_extras.py` rend cet invariant **exécutable** : la dérive casse
  un test, pas un déploiement.

## La couture (`api.py`) — la seule chose qu'un front voit

- `stream_reply(message, *, user_id, thread_id)` **cache entièrement** LangGraph
  (async generator de `str`). Rien de lang\* ne fuite.
- **Elle livre l'état TERMINAL du graphe**, pas les tokens des nœuds LLM — donc
  le message **déjà passé par `guard_output`**, en **un seul chunk**.
  ⚠️ Ne **jamais** revenir à un streaming token par token depuis `answer`/`model` :
  le garde de sortie est un nœud **postérieur**, streamer en direct afficherait
  au client ce que le garde allait caviarder. Garder ≠ streamer, c'est exclusif.
- Corollaire : la couture ne connaît **aucun nom de nœud** — elle lit l'état.
  Recâbler ou renommer le graphe ne peut pas la casser silencieusement.
- **Tout chemin livre exactement un chunk non vide** : réponse normale, input
  bloqué, plantage (`GRACEFUL_ERROR_MESSAGE`), escalade en pause
  (`ESCALATION_PENDING_MESSAGE`). Un front ne doit jamais voir un flux vide.
- Pont **sync→async par thread** (`asyncio.to_thread`) pour garder le checkpointer
  **sync** (donc le backend `sqlite` reste valide, pas d'`astream`/`AsyncSqliteSaver`
  imposé). Rôles : [`docs/streaming.md`](../../docs/streaming.md).

## La porte HTTP (`server.py`) — elle TRANSPORTE, elle ne pense pas

- `POST /chat` (SSE) + `/health` (liveness) + `/ready` (readiness). Lancer :
  `make serve`. Plan d'ensemble : [`docs/plan-deploiement-2026-07-25.md`](../../docs/plan-deploiement-2026-07-25.md).
- **Aucune logique d'agent ici.** Pas de graphe, pas de prompt, aucune décision sur
  la réponse. En ajouter créerait un 2e cerveau que seul le chemin HTTP atteint,
  et le CLI divergerait en silence. Toute logique remonte dans la couture.
- **Événements SSE typés** (`{"type": "chunk"|"error"|"done"}`) : B1.5 ajoutera des
  types (étapes, sources FAQ) sans casser les clients existants.
- Une **erreur voyage DANS le flux**, pas en code HTTP : le status part avec le 1er
  octet, donc un 200 déjà émis ne peut plus devenir un 500.
- **Deux identités à ne jamais confondre** : `API_KEY` autorise l'**appelant**
  (as-tu le droit d'utiliser cet agent ?) ; `user_id` désigne le **client** (de qui
  parle-t-on ?). Le second n'est pas encore prouvé — trou 🔴 isolé dans la seule
  fonction `_resolve_user_id`, refermé à l'étape 5 (token vérifié).
- **Démarrage fail-closed** : sans `API_KEY` et sans `API_ALLOW_UNAUTHENTICATED=true`
  explicite, le process **refuse de démarrer**. Ne pas « assouplir » ça : c'est ce
  qui empêche de déployer une porte ouverte par oubli de variable.
- 🎚️ **Le curseur local ⇄ réaliste** : `API_ALLOW_UNAUTHENTICATED` peut valoir
  `true` dans le `.env` de dev (l'auth devient du bruit quand on code une feature,
  et `make serve` n'écoute que sur `127.0.0.1`). Le conteneur, lui, **force `false`**
  via le bloc `environment` de `compose.yaml`, qui écrase `env_file`. Découpage
  **structurel, pas disciplinaire** : le cran de réalisme arrive avec Docker et ne
  peut pas être annulé par confort local. Ne pas retirer cette ligne du Compose.
- Corollaire : ce drapeau est une décision **du serveur**. Un client ne décide
  jamais s'il doit s'authentifier — il présente une clé, ou il n'en présente pas.
- `fastapi`/`uvicorn` sont un **extra** (`--extra server`) : le CLI et Chainlit
  n'ont pas à traîner un serveur web.

## L'image (`Dockerfile`) — se construit depuis la RACINE

- `make docker-up` (ou `docker build -f packages/support-agent/Dockerfile .`).
  ⚠️ Le contexte est la **racine du repo**, jamais ce dossier : le build a besoin
  de `uv.lock` + du `pyproject.toml` racine pour installer une tranche reproductible.
- **Multi-stage** : `uv sync --frozen --no-dev --no-install-workspace` met les deps
  lourdes dans une couche cachée à part, puis `--no-editable` installe notre code en
  wheel → le stage runtime ne copie que le `.venv`, sans les sources.
- **Non négociable dans l'image** : utilisateur **non-root**, **aucun `.env`**
  (les secrets s'injectent au runtime), `--host 0.0.0.0` dans le `CMD`,
  `PYTHONUNBUFFERED=1` (sinon les logs restent bloqués dans le tampon).
- `database/` = **volume**, jamais l'image. `data/kb-velmo` = source versionnée,
  donc **dans** l'image.
- Pièges déjà payés (détail : [`docs/plan-deploiement-2026-07-25.md`](../../docs/plan-deploiement-2026-07-25.md)) :
  un `--mount=type=bind` ne survit pas à son `RUN` · `env_file` de Compose **écrase**
  les `ENV` de l'image · même image de base obligatoire dans les deux stages.

## Carte du package (le COMMENT détaillé → [`docs/architecture.md`](../../docs/architecture.md))

`config.py` (Settings .env) · `llm/` factory + embeddings (agnostique) ·
`memory/` court terme (checkpointer / `thread_id`) + long terme (store / `user_id`) ·
`knowledge/` RAG FAQ · `graph/` nœuds + arêtes + `state` · `guardrails/`
(kill switch) · `actions/` (port métier) · `eval/` · `agent.py` (CLI) ·
`api.py` (la couture) · `server.py` (la porte HTTP).

## Faits opérationnels à garder en tête

- **FAQ = `InMemoryVectorStore` reconstruit à CHAQUE démarrage** (`knowledge/ingest.py`).
  Pas de re-vectorisation *par question* (seule la question est embeddée pour la
  recherche). Passer à un store persistant (Chroma / pgvector / Azure AI Search)
  = changement **d'un seul fichier** (`ingest.py`), l'agent ne bouge pas.
- **Persistance mémoire** (checkpointer + store) : switch `memory` / `sqlite` /
  `postgres` via `PERSISTENCE_BACKEND` (`.env`) — pas de code. Les trois sont
  câblés et vérifiés. En `postgres` : **une seule base**, les deux horizons
  séparés par schéma (`DATABASE_SCHEMA`), pgvector dedans, **pool partagé**
  (`memory/postgres_conn.py`) et *sweeper* de rétention RGPD (`MEMORY_TTL_DAYS`,
  compté depuis le **dernier accès**). ⚠️ `psycopg[binary]` obligatoire (sans
  libpq, échec **à l'import**) et image `pgvector/pgvector`, pas `postgres`.
- **Guardrails** : kill switch `GUARDRAILS_ENABLED` ; off = graphe identique à avant.
- Les providers **auto-découvrent** leurs `*_API_KEY` depuis `.env`.

## Méthode dans ce package

- **Context7 avant tout code lang\*** (langchain / langgraph / langsmith) — ces
  API bougent vite. C'est ici que la règle s'applique le plus.
- Lancer depuis la **racine** : `make run` / `make test` / `make check`.
- Une phase à la fois — la feuille de route scope A est [`ROADMAP.md`](../../ROADMAP.md)
  (racine) ; le scope B a [`docs/roadmap-frontend.md`](../../docs/roadmap-frontend.md).
