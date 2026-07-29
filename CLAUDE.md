# CLAUDE.md — Règles du jeu pour l'assistant de code

> Ce fichier est lu par Claude Code à chaque session. Il décrit le projet, la
> stack, les conventions et les contraintes à respecter. Garde-le à jour.

## Le projet en une phrase

Un **agent IA de support client** construit avec l'écosystème LangChain
(LangChain + LangGraph + LangSmith), doté de mémoire (court + long terme),
branché sur une **base de connaissance FAQ** (RAG), et **agnostique** au
fournisseur du LLM comme au projet métier.

## Contrainte n°1 : l'agnosticisme du LLM (NON NÉGOCIABLE)

- Le code applicatif ne doit **jamais** importer ou instancier un provider LLM
  en direct (pas de `ChatMistralAI(...)` disséminé dans le code métier).
- Tout passe par la **factory** `src/support_agent/llm/factory.py` qui renvoie
  un `BaseChatModel` à partir de la configuration (`.env`).
- Changer de provider (Mistral → Groq → Foundry → API maison) doit se faire en
  changeant **une variable d'environnement**, pas le code.

## Conventions

- **Docs & explications pédagogiques : en français.**
- **Code (noms, commentaires, docstrings, messages de log) : en anglais.**
- Python géré par **`uv`** (pas de pip/venv manuel).
- Version Python cible : **3.12** (compatibilité maximale de l'écosystème ; le
  système a 3.14 mais tous les wheels ne sont pas encore dispo dessus).
- Style : type hints partout, fonctions courtes, pas de config en dur.

## Structure

Mono-repo **uv workspace** (pourquoi : `docs/vision.md` ; comment physique :
`docs/architecture.md` §9). La racine n'est pas un package : elle orchestre les
membres via `[tool.uv.workspace]` et partage un unique `uv.lock` + `.venv`.

```
./                          # racine = workspace root (virtuel)
├── pyproject.toml          # [tool.uv.workspace] members = ["packages/*"] + dev
├── uv.lock                 # UN lockfile partagé
├── Makefile                # porte d'entrée unique (make run / test / check…)
├── data/                   # SOURCE versionnée, écrite par un humain : la FAQ
├── database/               # RUNTIME gitignoré, écrit par l'agent (cwd-relatif) :
│                           #   working_memory/ (thread_id) · agent_memory/ (user_id)
│                           #   → carte dev ↔ prod : database/README.md
├── docs/                   # spec.md (QUOI) + architecture.md (COMMENT) + vision.md
└── packages/
    └── support-agent/      # ⭐ Scope A — le cœur (l'agent)
        ├── pyproject.toml   # le package + ses deps (build hatchling)
        ├── src/support_agent/
        │   ├── config.py    # config typée (.env) — Pydantic Settings
        │   ├── llm/         # ⭐ cœur agnostique : factory + adaptateurs providers
        │   ├── memory/      # mémoire court terme (checkpointer) + long terme (store)
        │   ├── knowledge/   # base FAQ : ingestion, embeddings, retriever (RAG)
        │   ├── graph/       # orchestration LangGraph (nœuds, arêtes, state)
        │   ├── actions/     # port métier + 2 adaptateurs (dont sql/ = doublure)
        │   ├── guardrails/ · eval/
        │   ├── agent.py     # CLI interactif (assemblage du graphe)
        │   └── api.py       # ⭐ la couture stream_reply (porte de sortie, cache LangGraph)
        └── tests/
```

> Scope B (frontend) existe déjà : `packages/client/` — distribution
> `client-chainlit`, module `src/client_chainlit/` (coquille Chainlit). Chainlit
> est **une** implémentation de client, d'où le nom. Le scope C (backend) viendra
> comme `packages/backend`. Voir `docs/vision.md`.
>
> ⭐ Depuis l'**étape 4 du déploiement**, `packages/client` ne dépend **plus** de
> `support-agent` : il appelle l'agent en **HTTP** (`agent_client.py`, même
> signature que la couture). Ne pas réintroduire cet import — c'est la preuve du
> découplage, et elle est vérifiée par le build de l'image du client.
>
> 🧭 **Un `CLAUDE.md` par package.** Chaque membre porte son propre `CLAUDE.md`
> (règles **locales**), lu **à la demande** quand je travaille dans ce sous-arbre.
> Ce fichier racine reste la source des **invariants transverses** (toujours
> chargés) ; les fichiers de package **précisent**, ils ne remplacent pas.

## Velmo : un ancien starter abandonné (⚠️ pas une cible de travail)

**Ce dépôt-ci est LE livrable** — celui qui est rendu, celui qu'on soigne. Tout
le travail se fait ici.

`/Users/sabine/repo-local_git/brief15-velmo-v2-development-phase` (branche
`lang-suite`, seconde racine du workspace VSCode) est le **starter d'exercice
fourni en formation**. Sabine a commencé dessus, puis a bifurqué vers la piste
agnostique dans un dépôt neuf — celui-ci. **La piste Velmo est abandonnée**
(confirmé le 2026-07-29).

Règles :

- **Ne rien porter *vers* Velmo.** Ne pas traiter ses chantiers comme du travail
  à faire là-bas, ne pas y écrire de code, ne jamais commiter à cheval sur les
  deux dépôts (git, `pyproject.toml`, `uv.lock` et `.venv` distincts).
- **Les briefs = une checklist de conformité, pas un plan de portage.**
  `docs/brief/chantier{1,2,3}-*.md` (rédigés **ici**) servent à vérifier que ce
  projet reste dans les clous de l'exercice. Idem pour les tests d'acceptance du
  starter, gardés en `docs/brief/tests-reference/` (hors collecte pytest, voir
  son README) : `test_memory.py` valait le portage, `test_guardrails.py` et
  `test_mlops.py` sont les cahiers des charges des chantiers 2 et 3,
  `test_business.py` ne doit **jamais** être porté (domaine métier Velmo :
  remboursements, plafond 50 €, table d'escalade).
- **Un critère du starter qui contredit une décision documentée ici s'écarte en
  l'argumentant** (ex. le hors-périmètre n'est pas bloqué ; le garde de sortie
  caviarde au lieu de bloquer). Le brief laisse l'architecture à notre main.

## Commandes

Passe par le `Makefile` (porte d'entrée unique ; `make help` pour la liste) :

```bash
make setup    # uv sync + crée .env depuis .env.example
make run      # lance l'agent en CLI (uv run python -m support_agent.agent)
make serve    # expose l'agent en HTTP/SSE sur :8000
make ui       # lance l'UI Chainlit sur :8001 (client HTTP → a besoin de `make serve`)
make docker-up  # la pile complète en conteneurs : postgres + agent-api + client
              #   ⚠️ ports DISJOINTS du dev local : agent :8100, UI :8101 — Docker
              #   publie sur 0.0.0.0 et uvicorn écoute sur 127.0.0.1, donc pas
              #   d'Errno 48 : sans ça on croit tester sa pile locale et on
              #   interroge le conteneur, en silence.
make seed     # peuple la DOUBLURE métier (boutique SQL : commandes, clients,
              #   tickets). Idempotent ; `ARGS=--reset` pour tout reconstruire.
              #   ⚠️ Doublure ≠ base à nous : en prod on la DÉBRANCHE pour appeler
              #   l'API du marchand. Pas d'Alembic ici, c'est délibéré — on ne
              #   migre pas une fixture, on la jette (database/README.md).
              #   Activée par SUPPORT_BACKEND=sqlite (défaut : memory).
make consolidate  # distille les fils terminés en épisodes (mémoire épisodique,
              #   Phase 14). À BLANC par défaut ; `ARGS=--write` pour écrire.
              #   Le SEUL appel LLM de cette mémoire, et il est hors du tour client.
make test     # tests (pytest)
make lint     # ruff check
make check    # lint + tests
make score    # NOTE l'agent, écrit le rapport, BLOQUE sous le seuil (chantier
              #   MLOps). Hors ligne par défaut : ne note que les dimensions
              #   DÉTERMINISTES (mémoire, garde-fous) — c'est ce que la CI garde.
              #   `ARGS=--live` ajoute la qualité (appelle un vrai LLM) ;
              #   `ARGS=--degraded` note garde-fous coupés ;
              #   `ARGS=--update-baseline` accepte ce run comme nouveau niveau.
              #   ⚠️ La note globale n'est PAS la porte : les planchers durs et la
              #   baseline le sont (TODO_priorities.md §Chantier 7).
```

## Where things live (rappel rapide)

- « **On fait quoi ensuite, dans quel ordre ?** » → `TODO_priorities.md` ⭐
- « Quel est le besoin métier ? » → `docs/spec.md`
- « Pourquoi ce projet, et pourquoi 3 scopes ? » → `docs/vision.md`
- « Comment c'est conçu techniquement ? » → `docs/architecture.md`
- « Quels blocs on déploie, et quelle base pour quoi ? » → `docs/architecture-cible-2026-07-25.md`
- « Comment on conteneurise et on déploie (Docker → Azure) ? » → `docs/plan-deploiement-2026-07-25.md`
- « Quand la CI se déclenche, et ce qu'elle garde (ou pas) ? » → `docs/ci.md`
- « Quelles sont les étapes du tuto ? » → `ROADMAP.md` (scope A) · `docs/roadmap-frontend.md` (scope B)
- « À quoi doit ressembler le produit FINI (prod-grade 2026) ? » → `docs/perimetre-final.md`
- « Quels bugs / dettes connus traînent dans le code ? » → `docs/audit-code-2026-07-19.md`
- « Où en est la sécurité de la surface réseau (étapes 1→4) ? » → `docs/revue-securite-2026-07-25.md`
- « Quels défauts de **correction** (hors sécurité) restent ouverts ? » → `docs/revue-code-2026-07-25.md`
- « Qu'a donné la revue du chantier **escalade** ? » → `docs/80a6c9e-revue-code-2026-07-26.md`
- « Comment on choisit le provider LLM ? » → `.env` + `packages/support-agent/src/support_agent/llm/`
- « Quels types de mémoire, et où vivent-ils ? » → `docs/memoire.md`
- « Qui gère le streaming de la réponse (agent / API / front) ? » → `docs/streaming.md`
- « Pourquoi l'escalade ne met pas le graphe en pause ? » → `docs/escalade.md`
- « Pourquoi ~5 s avant la réponse ? » → `docs/latence.md` (investigation **close** ;
  renvoie vers `latence-patterns-prod.md` et `prompt-caching.md`)
- « Comment on travaille ensemble (méthode réutilisable) ? » → `docs/methodologie.md`
- « C'est quoi ce mot d'anglais tech ? » → `docs/glossaire.md`
- « Pourquoi cette structure de package (src layout, tiret/underscore) ? » → `docs/anatomie-package-workspace.md`

## Ce que Claude doit faire

- Expliquer le POURQUOI avant le COMMENT (l'utilisatrice apprend en faisant).
- Vérifier la doc à jour (Context7) avant d'écrire du code lang* — ces API
  bougent vite.
- Avancer **une phase à la fois** (voir ROADMAP.md), pas tout d'un coup.

## Reprendre après un `/clear` (ou en nouvelle session)

Ce fichier et la mémoire projet (`MEMORY.md`) sont rechargés automatiquement.
Pour reprendre le fil exact :

1. Lire `ROADMAP.md` → section **« Où on en est »** = état d'avancement à jour.
2. Reprendre à la première phase non cochée.
3. Respecter la méthode (voir `docs/methodologie.md`) : Context7 avant le code
   lang*, pédagogie du POURQUOI, une phase à la fois, commit par phase.

Prompt de reprise type à me donner :
> « On reprend le projet agent de support. Lis CLAUDE.md, MEMORY.md et
> ROADMAP.md, fais un point sur où on en est, puis reprenons à la prochaine phase. »
