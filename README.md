# Agnostic Consumer Support AI Agent

Agent IA de support client bâti sur **LangChain**, **LangGraph** et **LangSmith** :
mémoire court et long terme, base de connaissance FAQ (RAG), outils métier,
escalade humaine et garde-fous. Le fournisseur de LLM est **interchangeable** —
Mistral, Groq, Google, Azure AI Foundry ou toute API compatible OpenAI — via une
seule variable d'environnement.

## Prérequis

- [`uv`](https://docs.astral.sh/uv/)
- Python 3.12
- Une clé API pour au moins un fournisseur de LLM
- Optionnel : un compte [LangSmith](https://smith.langchain.com) pour le tracing
- Optionnel : Docker (pile conteneurisée)

## Installation

```bash
make setup   # uv sync + création du .env depuis .env.example
```

Éditer ensuite `.env` : renseigner `LLM_PROVIDER` et la clé correspondante.

## Utilisation

```bash
make run          # agent en CLI interactif
make serve        # API HTTP/SSE sur :8000
make ui           # UI Chainlit sur :8001 (nécessite `make serve`)
make docker-up    # pile complète : postgres + agent-api (:8100) + client (:8101)
```

`make help` liste toutes les cibles.

## Développement

```bash
make test     # pytest
make lint     # ruff check
make check    # lint + tests
make score    # évaluation notée, bloquante sous le seuil
make seed     # peuple la base métier SQLite de démonstration
```

## Configuration

Toutes les variables sont documentées dans `.env.example`. Les principales :

| Variable | Rôle | Défaut |
|---|---|---|
| `LLM_PROVIDER` | Fournisseur du modèle de chat | `openai_compatible` |
| `PERSISTENCE_BACKEND` | Mémoire : `memory` · `sqlite` · `postgres` | `memory` |
| `SUPPORT_BACKEND` | Backend métier : `memory` · `sqlite` | `memory` |
| `GUARDRAILS_ENABLED` | Active les garde-fous entrée/sortie | `true` |
| `API_KEY` | Authentifie les appelants de l'API HTTP | *(vide)* |

## Structure

Mono-repo **uv workspace** : la racine orchestre les membres et partage un unique
`uv.lock` et un unique `.venv`.

| Chemin | Contenu |
|---|---|
| [`packages/support-agent/`](packages/support-agent/README.md) | L'agent : graphe, mémoire, RAG, outils, garde-fous, évaluation, API HTTP |
| [`packages/client/`](packages/client/README.md) | Client de démonstration Chainlit, consommant l'agent en HTTP |
| [`data/`](data/eval/README.md) | Sources versionnées : base de connaissance FAQ et corpus d'évaluation |
| [`database/`](database/README.md) | État runtime écrit par l'agent (non versionné) |
| `.env.example` | Référence des variables de configuration |
