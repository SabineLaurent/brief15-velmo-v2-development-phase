# client-chainlit

Client de démonstration [Chainlit](https://docs.chainlit.io) pour l'agent de
support. UI de test, non destinée à la production.

Ce package ne dépend pas de `support_agent` : il appelle l'agent en HTTP/SSE via
[`agent_client.py`](src/client_chainlit/agent_client.py), qui expose la même
signature que l'interface `stream_reply` de l'agent.

```
navigateur ──► client (Chainlit) ──HTTP/SSE──► agent-api ──► graphe LangGraph
                                   AGENT_API_URL
```

La distribution s'appelle `client-chainlit` et le module `client_chainlit` :
Chainlit est une implémentation de client parmi d'autres.

## Lancement

Deux process sont nécessaires : l'agent et le client.

### En conteneurs

```bash
make docker-up      # postgres + agent-api + client
```

UI sur <http://localhost:8101>, agent sur `:8100`.

### En local

```bash
make serve          # terminal 1 — l'agent, sur :8000
make ui             # terminal 2 — l'UI Chainlit, sur :8001
```

`make ui` exécute, **depuis la racine du dépôt** :

```bash
uv run chainlit run packages/client/src/client_chainlit/app.py -w --port 8001
```

Le cwd doit être la racine : Chainlit y résout le chemin de `app.py` et y lit le
`.env`. Au premier lancement, Chainlit génère `chainlit.md` et `.chainlit/` dans
le cwd.

Si l'agent n'est pas démarré, l'UI affiche un message d'indisponibilité.

### Ports

| | Agent | UI |
|---|---|---|
| Local (`make serve` / `make ui`) | `8000` | `8001` |
| Conteneurs (`make docker-up`) | `8100` | `8101` |

Les jeux de ports sont disjoints pour éviter toute ambiguïté entre la pile locale
et la pile conteneurisée. À l'intérieur des conteneurs, les services écoutent sur
`8000` ; seule la publication côté hôte diffère.

## Configuration

| Variable | Rôle | Défaut |
|---|---|---|
| `AGENT_API_URL` | URL de l'agent | `http://localhost:8000` |
| `AGENT_API_KEY` | Clé de service envoyée en en-tête `X-API-Key` | vide = aucun en-tête |

## Identité

- `thread_id` (mémoire court terme) = identifiant de session Chainlit.
- `user_id` (mémoire long terme) = valeur fixe `demo-user` ; il n'y a pas
  d'authentification dans ce client de démonstration.
