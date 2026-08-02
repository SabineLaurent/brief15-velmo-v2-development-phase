# support-agent

Le cœur de l'agent de support : orchestration LangGraph (`StateGraph`), mémoire
court et long terme, RAG sur la FAQ, outils métier, escalade humaine, garde-fous,
évaluation, et exposition HTTP.

Membre du workspace uv — voir le `pyproject.toml` racine. Les commandes se lancent
depuis la **racine du dépôt** via le `Makefile`.

## Installation

Le package est installé par `make setup` à la racine. Les fournisseurs de LLM et
le serveur HTTP sont des extras optionnels :

```bash
uv sync --extra mistral      # ou groq, google, azure
uv sync --extra server       # fastapi + uvicorn
```

## Utilisation

```bash
make run      # CLI interactif
make serve    # API HTTP/SSE sur :8000
make test     # tests du package
make score    # évaluation notée
```

## Modules

| Chemin | Rôle |
|---|---|
| `config.py` | Configuration typée lue depuis `.env` (Pydantic Settings) |
| `llm/` | Factory de modèles de chat et d'embeddings, indépendante du fournisseur |
| `memory/` | Mémoire court terme (checkpointer, `thread_id`) et long terme (store, `user_id`) |
| `knowledge/` | Ingestion de la FAQ, embeddings, retriever |
| `graph/` | Nœuds, arêtes et état du graphe LangGraph |
| `actions/` | Port métier et ses adaptateurs (`memory`, `sqlite`) |
| `guardrails/` | Filtrage entrée/sortie |
| `eval/` | Corpus, datasets et scoring |
| `agent.py` | Point d'entrée CLI |
| `api.py` | Interface publique : `stream_reply(message, *, user_id, thread_id)` |
| `server.py` | API HTTP : `POST /chat` (SSE), `/health`, `/ready` |

## Interface publique

Tout consommateur externe passe par `api.stream_reply`, qui encapsule entièrement
LangGraph et renvoie un flux de chaînes de caractères :

```python
from support_agent.api import stream_reply

async for chunk in stream_reply("Où est ma commande ?", user_id="u1", thread_id="t1"):
    print(chunk, end="")
```

## API HTTP

| Endpoint | Description |
|---|---|
| `POST /chat` | Conversation, réponse en Server-Sent Events (`chunk` / `error` / `done`) |
| `GET /health` | Liveness |
| `GET /ready` | Readiness (dépendances initialisées) |

L'authentification se fait par l'en-tête `X-API-Key`, comparé à `API_KEY`. Sans
`API_KEY` ni `API_ALLOW_UNAUTHENTICATED=true`, le serveur refuse de démarrer.

## Configuration

Variables spécifiques au package, documentées dans `.env.example` :
`LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDINGS_PROVIDER`, `KNOWLEDGE_DIR`,
`PERSISTENCE_BACKEND`, `SUPPORT_BACKEND`, `GUARDRAILS_ENABLED`, `API_KEY`.

## Image Docker

Le `Dockerfile` se construit depuis la **racine du dépôt** (il a besoin de
`uv.lock` et du `pyproject.toml` racine) :

```bash
docker build -f packages/support-agent/Dockerfile .
make docker-smoke    # construit puis vérifie le démarrage réel des images
```
