# frontend — coquille de démo Chainlit (scope B, niveau 1)

But : **voir l'agent parler** dans un navigateur, pour un GIF de 20 s au README.
UI **assumée non-prod** (cf. `docs/roadmap-frontend.md`).

Ce membre ne connaît **rien** de LangGraph : il parle à l'agent uniquement par la
couture `stream_reply` (paquet `support_agent`). C'est ce qui le rend **jetable** —
on pourra remplacer Chainlit par un vrai front sans toucher au cerveau.

## Lancer

Depuis la **racine du repo** (pas depuis ce dossier) :

```bash
uv run chainlit run packages/frontend/src/frontend/app.py -w
```

Puis ouvrir `http://localhost:8000`. `Ctrl-C` pour arrêter.

### Décomposition de la commande

| Morceau | Rôle |
|---|---|
| `uv run` | Exécute dans le **`.venv` partagé** du workspace (après sync auto). C'est ce qui met `chainlit` + `support_agent` dans le PATH sans activer de venv à la main. |
| `chainlit run` | Sous-commande du CLI qui **démarre le serveur** de l'app (FastAPI/uvicorn + socket temps réel sous le capot) et sert la page de chat. |
| `packages/…/app.py` | La **cible** : le fichier que Chainlit charge pour **découvrir les handlers** décorés (`@cl.on_chat_start`, `@cl.on_message`). Chemin **relatif au cwd**. |
| `-w` (`--watch`) | **Rechargement à chaud** : Chainlit resurveille le fichier et recharge dès qu'on l'édite. Confort de dev (utile en B1.3) ; inutile en démo figée. |

### Pourquoi **depuis la racine** ?

Le cwd = la racine, car l'agent résout ses données en **chemins relatifs** :
`./data/faq` (la FAQ) et `./TEMP/database/agent_state.db` (la mémoire SQLite). Lancer
d'ailleurs casserait ces chemins.

> Détails : port par défaut `8000` (surcharge `--port 8765`) ; au 1er lancement
> Chainlit génère `chainlit.md` + `.chainlit/` à la racine (`.gitignore` jusqu'à
> B1.6). Une porte d'entrée `make ui` viendra aussi en B1.6.
