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
├── data/                   # runtime : FAQ (data/faq) + SQLite (chemins cwd-relatifs)
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
        │   ├── actions/ · guardrails/ · eval/
        │   ├── agent.py     # CLI interactif (assemblage du graphe)
        │   └── api.py       # ⭐ la couture stream_reply (porte de sortie, cache LangGraph)
        └── tests/
```

> Scope B (frontend) existe déjà : `packages/frontend/` (coquille Chainlit). Le
> scope C (backend) viendra comme `packages/backend`. Voir `docs/vision.md`.
>
> 🧭 **Un `CLAUDE.md` par package.** Chaque membre porte son propre `CLAUDE.md`
> (règles **locales**), lu **à la demande** quand je travaille dans ce sous-arbre.
> Ce fichier racine reste la source des **invariants transverses** (toujours
> chargés) ; les fichiers de package **précisent**, ils ne remplacent pas.

## Commandes

Passe par le `Makefile` (porte d'entrée unique ; `make help` pour la liste) :

```bash
make setup    # uv sync + crée .env depuis .env.example
make run      # lance l'agent (uv run python -m support_agent.agent)
make test     # tests (pytest)
make lint     # ruff check
make check    # lint + tests
```

## Where things live (rappel rapide)

- « **On fait quoi ensuite, dans quel ordre ?** » → `TODO_priorities.md` ⭐
- « Quel est le besoin métier ? » → `docs/spec.md`
- « Pourquoi ce projet, et pourquoi 3 scopes ? » → `docs/vision.md`
- « Comment c'est conçu techniquement ? » → `docs/architecture.md`
- « Quelles sont les étapes du tuto ? » → `ROADMAP.md` (scope A) · `docs/roadmap-frontend.md` (scope B)
- « À quoi doit ressembler le produit FINI (prod-grade 2026) ? » → `docs/perimetre-final.md`
- « Quels bugs / dettes connus traînent dans le code ? » → `docs/audit-code-2026-07-19.md`
- « Comment on choisit le provider LLM ? » → `.env` + `packages/support-agent/src/support_agent/llm/`
- « Quels types de mémoire, et où vivent-ils ? » → `docs/memoire.md`
- « Qui gère le streaming de la réponse (agent / API / front) ? » → `docs/streaming.md`
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
