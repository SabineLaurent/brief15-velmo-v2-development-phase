# 🎧 Agnostic Consumer Support AI Agent

Un agent IA de support client, construit pas à pas avec **LangChain**,
**LangGraph** et **LangSmith**. Il est doté de mémoire (court + long terme),
s'appuie sur une base de connaissance **FAQ** (RAG), et reste **agnostique** :
le LLM peut venir d'Azure AI Foundry, de Mistral, Google, Groq… ou de ta propre
API — on change de fournisseur sans toucher au code.

## Prérequis

- [`uv`](https://docs.astral.sh/uv/) (gestionnaire Python)
- Une clé API pour au moins un fournisseur de LLM (Mistral, Groq, Foundry…)
- (Optionnel mais recommandé) un compte [LangSmith](https://smith.langchain.com)
  pour tracer et déboguer l'agent

## Installation

```bash
make setup   # installe les dépendances (uv) ET crée .env depuis .env.example
#   → édite ensuite .env : choisis LLM_PROVIDER et renseigne la clé correspondante
```

> Tape `make` (ou `make help`) pour voir toutes les commandes disponibles.

## Lancement

```bash
make run     # lance l'agent de support
```

## Organisation du dépôt

Mono-repo **uv workspace** : la racine orchestre les membres et partage un unique
`uv.lock` et un unique `.venv`.

| Chemin | Contenu |
|---|---|
| [`packages/support-agent/`](packages/support-agent/README.md) | Le cœur de l'agent : graphe, mémoire, RAG, outils, garde-fous, évaluation |
| [`packages/client/`](packages/client/README.md) | Le client de démo (Chainlit), qui parle à l'agent en **HTTP** |
| [`data/`](data/eval/README.md) | Sources versionnées : base de connaissance FAQ + corpus d'évaluation |
| [`database/`](database/README.md) | État runtime écrit par l'agent (non versionné) |
| `.env.example` | Toutes les variables de configuration, commentées |

## Statut

Fonctionnalités en place : LLM agnostique, observabilité LangSmith, mémoire court
+ long terme, RAG / FAQ, orchestration LangGraph, escalade humaine, outils &
actions métier, évaluation & scoring, persistance (SQLite / Postgres + pgvector),
sécurité & garde-fous, exposition HTTP et conteneurisation.
