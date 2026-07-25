# client-chainlit — coquille de démo Chainlit (scope B, niveau 1)

But : **voir l'agent parler** dans un navigateur, pour un GIF de 20 s au README.
UI **assumée non-prod** (cf. `docs/roadmap-frontend.md`).

Ce membre ne connaît **rien** de LangGraph — ni même du paquet `support_agent`
depuis l'**étape 4 du déploiement** : il parle à l'agent par un **appel HTTP**, à
travers la couture `stream_reply` réimplémentée en client réseau
([`agent_client.py`](src/client_chainlit/agent_client.py)). C'est ce qui le rend
**jetable** — on pourra remplacer Chainlit par un vrai front sans toucher au cerveau.

```
navigateur ──► client (Chainlit) ──HTTP/SSE──► agent-api ──► [ le graphe, caché ]
                 ce paquet          AGENT_API_URL
```

> ⭐ La bascule import → HTTP a coûté **une ligne** dans `app.py`, parce que le
> client réseau expose exactement la même signature. C'est la démonstration que la
> couture valait son coût ; le détail est dans
> [`docs/plan-deploiement-2026-07-25.md`](../../docs/plan-deploiement-2026-07-25.md) §Étape 4.

> 📛 **Pourquoi « client-chainlit » et pas « client » ?** Chainlit est **une**
> implémentation de client, pas *le* client. Le nom le dit, pour qu'ajouter un
> second client (React, mobile, `curl`) ne demande pas de renommer celui-ci.
> Distribution avec tiret, module avec underscore (`src/client_chainlit/`) :
> convention expliquée dans [`docs/anatomie-package-workspace.md`](../../docs/anatomie-package-workspace.md).

## Lancer

Depuis l'étape 4, il faut **deux process** : le client ne contient plus l'agent.

### En conteneurs (le plus proche du réel)

```bash
make docker-up      # postgres + agent-api + client, dans l'ordre
```

Puis ouvrir **`http://localhost:8001`** (l'agent occupe le `8000`).

### Sur la machine, en deux terminaux

```bash
make serve          # terminal 1 — l'agent, sur :8000
make ui             # terminal 2 — l'UI Chainlit, sur :8001
```

Sous le capot, `make ui` fait, depuis la **racine du repo** (pas depuis ce dossier) :

```bash
uv run chainlit run packages/client/src/client_chainlit/app.py -w --port 8001
```

`Ctrl-C` pour arrêter. Si l'agent ne tourne pas, l'UI répond « service
momentanément injoignable » : c'est le comportement attendu d'un client dont le
serveur est absent, pas un bug.

**Les deux variables que ce client lit** (cf. `.env.example` §9) :

| Variable | Rôle | Défaut |
|---|---|---|
| `AGENT_API_URL` | **La seule chose** qu'il sait de l'agent | `http://localhost:8000` |
| `AGENT_API_KEY` | La clé de service qu'il **présente** (≠ `user_id`) | vide = aucun en-tête |

### Décomposition de la commande

| Morceau | Rôle |
|---|---|
| `uv run` | Exécute dans le **`.venv` partagé** du workspace (après sync auto). C'est ce qui met `chainlit` dans le PATH sans activer de venv à la main. |
| `chainlit run` | Sous-commande du CLI qui **démarre le serveur** de l'app (FastAPI/uvicorn + socket temps réel sous le capot) et sert la page de chat. |
| `packages/…/app.py` | La **cible** : le fichier que Chainlit charge pour **découvrir les handlers** décorés (`@cl.on_chat_start`, `@cl.on_message`). Chemin **relatif au cwd**. |
| `-w` (`--watch`) | **Rechargement à chaud** : Chainlit resurveille le fichier et recharge dès qu'on l'édite. Confort de dev (utile en B1.3) ; inutile en démo figée. |

### Pourquoi **depuis la racine** ?

Le cwd sert désormais à **deux** choses : Chainlit y résout le chemin de `app.py`
(donné en relatif) et y lit le `.env`. C'est aussi de là que `make serve` doit être
lancé, car l'**agent**, lui, résout ses données en chemins relatifs :
`./data/kb-velmo` (la base de connaissance, cf. `KNOWLEDGE_DIR`) et `./database/`
(l'état runtime — conversations et souvenirs, cf.
[`database/README.md`](../../database/README.md)).

> ℹ️ **Ce qui a changé à l'étape 4 :** ce paquet ne lit plus *lui-même* ni
> `./data/` ni `./database/` — il n'a plus l'agent dans son process. Ces chemins
> sont l'affaire du conteneur `agent-api`.

> Détails : au 1er lancement, Chainlit génère `chainlit.md` + `.chainlit/` **dans
> le cwd** (`.gitignore` jusqu'à B1.6) — et il le fait **à l'import** de son CLI,
> ce qui exige un cwd inscriptible (voir le `Dockerfile`, `WORKDIR /home/appuser`).
