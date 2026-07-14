# 🎧 Agnostic Consumer Support AI Agent

Un agent IA de support client, construit pas à pas avec **LangChain**,
**LangGraph** et **LangSmith**. Il est doté de mémoire (court + long terme),
s'appuie sur une base de connaissance **FAQ** (RAG), et reste **agnostique** :
le LLM peut venir d'Azure AI Foundry, de Mistral, Google, Groq… ou de ta propre
API — on change de fournisseur sans toucher au code.

> 📚 Ce dépôt est aussi un **tutoriel pédagogique**. Suis `ROADMAP.md` phase par
> phase pour apprendre l'écosystème LangChain en construisant.

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

## Documentation

| Fichier | Contenu |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | Le tuto étape par étape — **commence ici** |
| [`docs/spec.md`](docs/spec.md) | Le **QUOI** : périmètre fonctionnel, cas d'usage |
| [`docs/architecture.md`](docs/architecture.md) | Le **COMMENT** : conception technique, mémoire, agnosticisme |
| [`CLAUDE.md`](CLAUDE.md) | Règles & conventions du projet |

## Statut

🚧 En construction — Phase 0 (structure du projet) terminée.
